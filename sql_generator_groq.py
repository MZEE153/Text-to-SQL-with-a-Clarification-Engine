"""Step 5, Groq variant: same job as sql_generator.py, backed by Groq
instead of Gemini -- the practical fix for this session's chain of
free-tier interruptions (Gemini's 20/day cap, two HF Inference Providers
dead ends, then CPU-only Ollama proving too slow for iterative testing).

Groq supports native tool-calling, so with_structured_output works the
same way it does for Gemini -- no PydanticOutputParser fallback needed
here, unlike the HF attempt.
"""
from datetime import date  # standard library: today's date (the model cannot know it)
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so GROQ_API_KEY becomes available
from langchain_groq import ChatGroq  # LangChain's wrapper around Groq's hosted chat models

from sql_generator import SYSTEM_PROMPT, SQLGenerationResult  # reuse the SAME prompt and result schema as the Gemini version -- only the model differs
from business_rules import BUSINESS_RULES  # domain rules shared with the ambiguity classifier (cancelled orders are not sales)
from schema_retrieval import retrieve_relevant_tables  # Step 3b: embedding search returning the tables relevant to a question

load_dotenv()  # load .env now, before the Groq client is created below

model = ChatGroq(model="openai/gpt-oss-20b", temperature=0.1)  # Groq client; gpt-oss-20b = model currently in this account's catalog; low temperature = more deterministic SQL
generator = model.with_structured_output(SQLGenerationResult)  # parse the reply into a SQLGenerationResult (Groq supports tool-calling natively)


def generate_sql(resolved_question: str) -> SQLGenerationResult:  # main entry point: one resolved question in, one typed result out
    relevant = retrieve_relevant_tables(resolved_question, top_k=4)  # top 4 tables by similarity (all 4 today)
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)  # keep only the description text of each table, blank-line separated
    today_line = f"Today's date is {date.today().isoformat()}. Use it to turn relative dates such as 'last year' into explicit calendar years; never guess the current year.\n\n"  # models have no clock; resolved questions may say "last year"
    system = today_line + BUSINESS_RULES + SYSTEM_PROMPT.format(schema=schema_text)  # today's date, then the shared business rules, then the shared prompt with its {schema} placeholder filled in
    return generator.invoke([("system", system), ("human", resolved_question)])  # call Groq with system + user messages; returns a SQLGenerationResult


def print_result(resolved_question: str) -> None:  # helper: generate and pretty-print for manual testing
    print(f"Resolved question: {resolved_question}")  # echo the input question
    result = generate_sql(resolved_question)  # run generation
    print(f"  tables_used: {result.tables_used}")  # tables the model says it used
    print(f"  explanation: {result.explanation}")  # model's plain-English explanation
    print(f"  sql:\n{result.sql}")  # the generated SQL
    print()  # blank line between results


if __name__ == "__main__":  # only runs when executed directly, not when imported
    test_questions = [  # same 4 regression questions used for the Gemini version
        "How many systems signed off last year?",  # expected answer: 12
        "Who was the best customer last year, defined as highest revenue?",  # expected: Acme Corp
        "Who was the best customer last year, defined as highest total profit?",  # expected: Stark Industries
        "What is the total sales in all years? Assume completed orders only, summed across all time.",  # expected: 3,077,465.82
    ]
    for q in test_questions:  # run each question in turn
        print_result(q)  # generate and print SQL for it
