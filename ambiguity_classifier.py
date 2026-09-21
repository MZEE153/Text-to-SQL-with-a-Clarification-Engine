"""Step 4: given a natural-language question + the relevant schema, decide
how to handle it. Three outcomes, not two -- see AmbiguityCheck below --
because a question can be technically underspecified without being worth
interrupting the user over.
"""
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so GOOGLE_API_KEY becomes available
from langchain_google_genai import ChatGoogleGenerativeAI  # LangChain's wrapper around Google's Gemini chat models
from pydantic import BaseModel, Field  # BaseModel = a typed data class; Field = attaches a description/metadata to one field

from schema_retrieval import retrieve_relevant_tables  # Step 3b: embedding search returning the tables most relevant to a question

load_dotenv()  # load .env now, before the Gemini client is created below

# What each part of SYSTEM_PROMPT below does (comments can't go INSIDE the string -- they'd be sent to the model as text):
#   "You are the ambiguity-detection stage..." -> gives the model its role; "You are NOT writing SQL" keeps it in its lane
#   "THREE possible outcomes, not two"         -> introduces the 3-way classification (added in the calibration fix)
#   "1. FULLY UNAMBIGUOUS"                     -> clear question: is_ambiguous=False, assumed_default=null
#   "2. UNDERSPECIFIED BUT HAS A SENSIBLE DEFAULT" -> proceed without asking, but state the assumption (assumed_default) for transparency
#   "3. GENUINELY AMBIGUOUS"                   -> several equally plausible readings: is_ambiguous=True and propose 2-5 interpretations
#   "The test for choosing between 2 and 3"    -> the "would a reasonable person be annoyed by a silent default?" tie-breaker
#   "A common source of genuine ambiguity"     -> hints that words like best/top/most active usually mean outcome 3
#   "If genuinely ambiguous, each interpretation needs..." -> each option needs a label plus an SQL-ready one-sentence definition
#   "{schema}"                                 -> placeholder filled with the retrieved table descriptions by .format() in check_ambiguity()
SYSTEM_PROMPT = """You are the ambiguity-detection stage of a Text-to-SQL system.
You are NOT writing SQL. Your only job is to decide how to handle an
underspecified question, given the database schema below. There are THREE
possible outcomes, not two:

1. FULLY UNAMBIGUOUS -- the question maps cleanly to one query with nothing
   to assume. is_ambiguous=False, assumed_default=null.

2. UNDERSPECIFIED BUT HAS A SENSIBLE DEFAULT -- the question is technically
   open to more than one reading, but one interpretation is what most
   reasonable people would obviously mean by default, and picking it
   without asking would rarely cause real confusion (e.g. "total sales"
   defaulting to completed orders, summed across all recorded time).
   is_ambiguous=False, but set assumed_default to a precise one-sentence
   statement of exactly what you assumed, so it can be shown to the user
   alongside the final answer for transparency.

3. GENUINELY AMBIGUOUS -- multiple interpretations are all similarly
   plausible, there is no single "most people would mean this" default,
   and guessing wrong would give a fundamentally different, misleading
   answer (not just a minor variation). is_ambiguous=True, propose 2-5
   concrete interpretations.

The test for choosing between outcome 2 and outcome 3: would a reasonable
person be mildly surprised or annoyed if you proceeded with a default
without asking? If yes (the alternatives are all equally "normal", no
clear default exists) -> outcome 3, ask. If no (there's an obvious default
and being wrong is a minor, easily-corrected issue) -> outcome 2, proceed
with an explicit stated assumption.

A common source of genuine (outcome 3) ambiguity: subjective/superlative
words like "best", "top", "most active" that could map to fundamentally
different metrics (revenue vs. profit vs. order count) with no obvious
"normal" choice among them.

If genuinely ambiguous (outcome 3), each interpretation needs a short
user-facing label and a precise one-sentence definition of exactly what
it means computationally, specific enough that someone could write the
SQL from it directly.

Database schema (relevant tables only):
{schema}
"""


class Interpretation(BaseModel):  # one possible meaning of an ambiguous question (shown to the user as a choice)
    label: str = Field(description="Short label shown to the user, e.g. 'Highest revenue'")  # short button-style name for this option
    detail: str = Field(  # the precise definition behind that label
        description="Precise one-sentence definition of this interpretation, specific "  # description text is sent to the model as instructions for this field
        "enough to write SQL from directly, e.g. 'The customer whose completed orders "  # "specific enough to write SQL from" is what lets Step 5 use it directly
        "sum to the highest total_amount in the given period.'"  # worked example of a good detail string
    )


class AmbiguityCheck(BaseModel):  # the exact shape the classifier's answer must take; structured output enforces it
    is_ambiguous: bool = Field(  # field 1: the branch flag the pipeline will switch on (ask the user vs. proceed)
        description="True ONLY when there is no single interpretation most reasonable "  # description text is sent to the model as instructions for this field
        "people would assume by default -- multiple materially different, similarly "  # what "genuinely ambiguous" means
        "plausible readings exist, and guessing wrong would give a fundamentally "  # ... and why guessing wrong is costly
        "different, misleading answer. If a sensible default exists, this is False "  # ... with a sensible default this must be False ...
        "even if the question is technically underspecified -- use assumed_default "  # ... even when the question is underspecified ...
        "instead of asking."  # ... and the default goes in assumed_default instead
    )
    reasoning: str = Field(description="One or two sentences explaining the decision.")  # field 2: the model's justification (useful for debugging and tracing)
    assumed_default: str | None = Field(  # field 3: the stated assumption; None (null) when there was nothing to assume
        default=None,  # optional field: defaults to None when the model leaves it out
        description="Set ONLY when is_ambiguous is False but the question was "  # description text is sent to the model as instructions for this field
        "technically underspecified and a reasonable default had to be picked to "  # only fill it when a default really was picked
        "proceed (e.g. 'Assuming completed orders only, summed across all time'). "  # example of a good stated assumption
        "Leave null when the question was fully unambiguous with nothing to assume, "  # null for clear questions
        "or when is_ambiguous is True.",  # null when we are asking instead of assuming
    )
    interpretations: list[Interpretation] = Field(  # field 4: the options to show the user when the question is ambiguous
        default_factory=list,  # default is a fresh empty list (a mutable default must go through default_factory)
        description="2-5 distinct interpretations if is_ambiguous is True; empty otherwise.",  # 2-5 options when asking, none otherwise
    )


model = ChatGoogleGenerativeAI(model="gemini-3.6-flash")  # creates the Gemini client (reads GOOGLE_API_KEY from the environment)
classifier = model.with_structured_output(AmbiguityCheck)  # wraps the model so its reply is parsed into an AmbiguityCheck object (tool-calling under the hood)


def check_ambiguity(question: str) -> AmbiguityCheck:  # main entry point: raw user question in, typed classification out
    # top_k=4 with only 4 tables in the schema means "no real filtering" --
    # every table comes back every time. That's deliberate: it keeps this
    # step using the same retrieve_relevant_tables() path Step 5 will use,
    # instead of a special-cased "just list everything" bypass. As the
    # schema grows past 4 tables, this same call starts filtering for real
    # with no code change needed.
    relevant = retrieve_relevant_tables(question, top_k=4)  # embedding search; returns all 4 tables today, filters for real if the schema grows
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)  # keep only each table's description text (drop name and score), blank-line separated
    system = SYSTEM_PROMPT.format(schema=schema_text)  # fill the {schema} placeholder in the prompt with those descriptions
    return classifier.invoke([("system", system), ("human", question)])  # call Gemini with system + user messages; returns an AmbiguityCheck


def print_result(question: str) -> None:  # helper: classify a question and pretty-print the outcome for manual testing
    print(f"Question: {question}")  # echo the input question
    result = check_ambiguity(question)  # run the classifier
    print(f"  is_ambiguous: {result.is_ambiguous}")  # the branch flag
    print(f"  reasoning: {result.reasoning}")  # the model's justification
    if result.is_ambiguous:  # outcome 3: the user should be asked to choose
        for opt in result.interpretations:  # list each candidate interpretation
            print(f"    - {opt.label}: {opt.detail}")  # label and its precise definition
    elif result.assumed_default:  # outcome 2: proceeding, but a default was assumed
        print(f"  assumed_default: {result.assumed_default}")  # show the assumption for transparency
    print()  # blank line between results


if __name__ == "__main__":  # only runs when executed directly (python ambiguity_classifier.py), not when imported
    # Known regression cases -- run these first every time.
    test_questions = [  # fixed questions covering each outcome
        "How many systems signed off last year?",  # clear -> expected: not ambiguous, nothing assumed
        "Who was the best customer last year?",  # "best" -> expected: ambiguous (revenue / order count / profit)
        "What products have the highest profit margin?",  # expected: not ambiguous
        "what is the name of the computer?",  # doesn't map cleanly to the schema -> known open gap (no "out of scope" state yet)
    ]
    for question in test_questions:  # run each regression question
        print_result(question)  # classify and print

    # Then drop into an interactive loop for your own questions.
    print("--- Type your own questions below (type 'exit' to quit) ---")  # switch from fixed tests to interactive mode
    while True:  # keep asking until the user types exit
        user_question = input("Your question: ")  # read one question from the terminal
        if user_question == "exit":  # exit keyword ends the session
            break  # leave the loop
        print_result(user_question)  # classify and print the typed question
