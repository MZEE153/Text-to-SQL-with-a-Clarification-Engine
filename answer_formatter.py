"""Step 8: turn the rows the database returned into a plain-English answer.

The risk at this stage is the model stating something the data does not
say, so the design keeps anything that MUST appear out of the model's hands:
  - the assumption from Step 4 and the "result was cut off" note are appended
    by code, so the model cannot forget or reword them
  - an empty result never reaches the model at all
  - every number in the model's sentence is checked against the rows it was
    shown, and anything unmatched is flagged to the user instead of trusted
"""
import json  # standard library: serialise the rows so the model sees them as structured data
import re  # standard library: regular expressions, used to find numbers inside text
from dataclasses import dataclass  # a lightweight typed container for the final answer
from decimal import Decimal  # exact decimal arithmetic, so 50000.10 and 50,000.1 compare as equal

from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so GROQ_API_KEY becomes available
from langchain_groq import ChatGroq  # LangChain's wrapper around Groq's hosted chat models
from pydantic import BaseModel, Field  # BaseModel = a typed data class; Field = attaches a description to one field

from sql_executor import QueryResult, MAX_ROWS  # Step 7: the result type this stage consumes, and the row cap (used in the "cut off" note)

load_dotenv()  # load .env now, before the Groq client is created below

PROMPT_ROWS = 25  # show the model at most this many rows, to keep the prompt small; the rest are simply not described

# What each rule in SYSTEM_PROMPT below does (comments can't go INSIDE the string -- they'd be sent to the model as text):
#   "answer-writing stage... ALREADY been run"  -> role: it only words results, it never queries or decides anything
#   "one to three plain-English sentences"      -> keeps answers short and direct
#   "Use ONLY facts in the rows"                -> the anti-hallucination rule
#   "Quote figures exactly... never round"      -> the numeric-grounding check later relies on numbers being copied, not rewritten
#   "Do not add currency symbols, years..."     -> the schema has no currency, and years not in the data would be guesses
#   "If several rows are shown, mention each"   -> stops it silently dropping rows from a list answer
#   "Never mention SQL, tables, columns"        -> written for a business reader
#   "If told the result was cut off..."         -> a partial result must not be presented as a complete total
#   "Do not restate assumptions or caveats"     -> code appends those itself, so the model must not duplicate or reword them
SYSTEM_PROMPT = """You are the answer-writing stage of a Text-to-SQL system.
A database query has ALREADY been run. You are given the user's question and
the exact rows it returned. Write the answer the user will read.

Rules:
- Answer in one to three plain-English sentences. Lead with the direct answer.
- Use ONLY facts in the rows. Every name and number you write must come
  straight from them.
- Quote figures exactly as given. You may add thousands separators, but never
  round, convert units, or change a digit, and never compute a new figure
  (no sums, averages, differences or percentages).
- Do not add currency symbols, units, years or dates that are not in the rows
  or in the question.
- If several rows are shown, mention each one (a short list is fine if there
  are more than three). Describe only the rows you are shown.
- Never mention SQL, tables, columns or how the query worked; write for a
  business reader.
- If you are told the result was cut off, do not state totals or counts for
  the full result.
- Do not restate assumptions or caveats; they are added separately.
"""


class AnswerDraft(BaseModel):  # the shape the model's reply must take; structured output enforces it
    answer: str = Field(description="The plain-English answer, one to three sentences, using only facts from the rows.")  # the single field: the sentence(s) shown to the user


model = ChatGroq(model="openai/gpt-oss-20b", temperature=0)  # Groq client; temperature 0 = as deterministic as possible, since this stage should only reword data
writer = model.with_structured_output(AnswerDraft)  # wraps the model so its reply is parsed into an AnswerDraft


@dataclass  # auto-generates __init__ and __repr__ for a plain data holder
class FinalAnswer:  # what this stage returns
    text: str  # the complete user-facing answer, including any appended assumption / cut-off / warning lines
    used_llm: bool  # False when the answer was produced without calling the model (empty result)
    ungrounded_numbers: list[str]  # numbers in the model's sentence that could not be matched to the data


NUMBER_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")  # a number with thousands separators (50,000.01) or a plain one (12, 3.5); the grouped form is tried first


def _canonical(token: str) -> str:  # normalise a number so different spellings of the same value compare equal
    value = Decimal(token.replace(",", ""))  # drop thousands separators and parse exactly (no floating-point error)
    return format(value.normalize(), "f")  # strip trailing zeros and write without exponent: 50000.10 -> '50000.1', '01' -> '1', '100' -> '100'


def numbers_in(text: str) -> set[str]:  # every number mentioned in a piece of text, in canonical form
    return {_canonical(t) for t in NUMBER_PATTERN.findall(text)}  # find all numeric tokens, canonicalise each, and deduplicate


def find_ungrounded_numbers(answer: str, allowed_text: str) -> list[str]:  # numbers the answer states that appear nowhere in the allowed source text
    missing = numbers_in(answer) - numbers_in(allowed_text)  # set difference: in the answer, but not in the data/question/SQL
    return sorted(missing, key=Decimal)  # sorted numerically so the output is stable and readable


def compose_text(answer: str, assumed_default: str | None, truncated: bool, ungrounded: list[str]) -> str:  # assemble the final user-facing text; pure, so it can be tested without any model
    parts = [answer]  # the model's (or the fixed) sentence always comes first
    if ungrounded:  # the model stated numbers that are not in the data
        parts.append(f"Warning: the number(s) {', '.join(ungrounded)} in the summary above could not be matched to the query result, so treat them with caution.")  # make the doubt visible instead of hiding it
    if truncated:  # the executor dropped rows beyond MAX_ROWS
        parts.append(f"Note: the result was cut off at {MAX_ROWS} rows, so there may be more.")  # a partial result must never look like a complete one
    if assumed_default:  # Step 4 had to assume something to proceed
        parts.append(f"Assumption: {assumed_default}")  # transparency: always shown, worded by Step 4, never by this model
    return "\n\n".join(parts)  # blank line between parts


def format_answer(question: str, sql: str, result: QueryResult, assumed_default: str | None = None) -> FinalAnswer:  # main entry point
    if not result.rows:  # the query ran fine but matched nothing
        text = compose_text("No matching records were found.", assumed_default, False, [])  # fixed wording; asking a model to describe "nothing" invites invention
        return FinalAnswer(text=text, used_llm=False, ungrounded_numbers=[])  # no model call at all

    shown = result.rows[:PROMPT_ROWS]  # the rows the model will actually see
    data_json = json.dumps({"columns": result.columns, "rows": shown}, default=str)  # rows as JSON; default=str turns Decimal and date values into exact strings (no float rounding)
    notes = []  # extra facts about the data the model must respect
    if result.truncated:  # the executor stopped at MAX_ROWS
        notes.append(f"The query returned MORE than {MAX_ROWS} rows; only the first {MAX_ROWS} were kept.")  # so the model never presents a partial result as complete
    if len(result.rows) > len(shown):  # we are showing fewer rows than we kept
        notes.append(f"Only the first {len(shown)} of {len(result.rows)} kept rows are shown to you.")  # so it does not describe rows it cannot see
    human = f"Question: {question}\n" + "".join(f"{n}\n" for n in notes) + f"Result (JSON):\n{data_json}"  # the user message: question, any notes, then the data

    draft = writer.invoke([("system", SYSTEM_PROMPT), ("human", human)])  # ask Groq to word the answer; returns an AnswerDraft
    allowed = " ".join([data_json, question, sql, assumed_default or ""])  # everything a number in the answer may legitimately come from (the SQL is included because a year literal there is grounded)
    ungrounded = find_ungrounded_numbers(draft.answer, allowed)  # any number the model stated that is not in that text
    return FinalAnswer(  # package everything up
        text=compose_text(draft.answer, assumed_default, result.truncated, ungrounded),  # final text with the appended lines
        used_llm=True,  # the model was called
        ungrounded_numbers=ungrounded,  # kept separately so callers and tests can inspect it
    )
