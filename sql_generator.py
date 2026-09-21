"""Step 5: given a fully-resolved (already unambiguous) question + the
relevant schema, generate a single PostgreSQL SELECT statement. Structured
output again -- callers get a typed result, not a chat blob to parse.

This stage does NOT re-judge ambiguity -- that already happened in Step 4.
It receives either a naturally-clear question, or a question that's already
been merged with a clarification answer / stated assumption.
"""
from dotenv import load_dotenv  # reads KEY=VALUE lines from the .env file so we can use them as environment variables
from langchain_google_genai import ChatGoogleGenerativeAI  # LangChain's wrapper around Google's Gemini chat models
from pydantic import BaseModel, Field  # BaseModel = a typed data class; Field = attaches a description/metadata to one field

from schema_retrieval import retrieve_relevant_tables  # Step 3b: embedding search that returns the tables most relevant to a question

load_dotenv()  # actually loads .env now, so GOOGLE_API_KEY is available when the Gemini client is created below

# What each part of SYSTEM_PROMPT below does (comments can't go INSIDE the string -- they'd be sent to the model as text):
#   "You are the SQL generation stage..." -> gives the model its role in the pipeline
#   "already been fully resolved..."       -> tells it NOT to re-judge ambiguity (Step 4 already did that)
#   "Output a single PostgreSQL SELECT"    -> forces read-only, one statement (first line of defense; Step 6 re-checks it anyway)
#   "Only reference tables and columns..." -> anti-hallucination rule: only names that exist in the schema text
#   "Be precise about filters..."          -> business rules: 'sales'/'orders' mean status='completed'; 'last year' = calendar year
#   "Prefer explicit column lists"         -> style rule: avoid SELECT *
#   "Use standard PostgreSQL syntax"       -> dialect rule (matches our Postgres 16 container)
#   "{schema}"                             -> placeholder; filled with the retrieved table descriptions by .format() in generate_sql()
SYSTEM_PROMPT = """You are the SQL generation stage of a Text-to-SQL system.
The question you receive has already been fully resolved -- any ambiguity
has already been settled, either because it was always clear or because
the user already answered a clarification question. Do not re-interpret
or second-guess the question; write SQL for exactly what it says.

Rules:
- Output a single PostgreSQL SELECT statement. Never write, update,
  delete, or alter data -- read-only queries only.
- Only reference tables and columns that literally appear in the schema
  below. Never invent a table or column name.
- Be precise about filters implied by the question -- e.g. "orders" or
  "sales" usually means status = 'completed' unless the question
  explicitly says otherwise; a "last year" / "this year" reference means
  filtering the relevant date column to that calendar year.
- Prefer explicit column lists over SELECT * where practical.
- Use standard PostgreSQL syntax.

Database schema (relevant tables only):
{schema}
"""


class SQLGenerationResult(BaseModel):  # the exact shape the LLM's answer must take; structured output enforces it
    sql: str = Field(  # field 1: the generated SQL text (a plain string)
        description="A single PostgreSQL SELECT statement that answers the question. "  # this description is sent to the model as instructions for this field
        "No markdown code fences, no trailing commentary -- raw SQL only."  # stops the model wrapping SQL in ```sql fences, which would break parsing
    )
    tables_used: list[str] = Field(description="Every table name referenced in the SQL.")  # field 2: list of table names -- lets us cross-check against the SQL later
    explanation: str = Field(  # field 3: a human-readable summary of what the query does
        description="One or two plain-English sentences explaining what the query computes."  # shown to the user later alongside the answer
    )


model = ChatGoogleGenerativeAI(model="gemini-3.6-flash")  # creates the Gemini client (reads GOOGLE_API_KEY from the environment)
generator = model.with_structured_output(SQLGenerationResult)  # wraps the model so its reply is parsed into a SQLGenerationResult object (tool-calling under the hood)


def generate_sql(resolved_question: str) -> SQLGenerationResult:  # main entry point: one resolved question in, one typed result out
    relevant = retrieve_relevant_tables(resolved_question, top_k=4)  # embedding search; top_k=4 returns all 4 tables today, filters for real if the schema grows
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)  # keep only each table's description text (drop name and score), separated by blank lines
    system = SYSTEM_PROMPT.format(schema=schema_text)  # fill the {schema} placeholder in the prompt with those descriptions
    return generator.invoke([("system", system), ("human", resolved_question)])  # call Gemini with system + user messages; returns a SQLGenerationResult


def print_result(resolved_question: str) -> None:  # helper: run generation and pretty-print it for manual testing
    print(f"Resolved question: {resolved_question}")  # echo the input question
    result = generate_sql(resolved_question)  # run the full generation step
    print(f"  tables_used: {result.tables_used}")  # show which tables the model says it used
    print(f"  explanation: {result.explanation}")  # show the model's plain-English explanation
    print(f"  sql:\n{result.sql}")  # show the generated SQL itself
    print()  # blank line between results


if __name__ == "__main__":  # only runs when executed directly (python sql_generator.py), not when imported by other files
    # These are hand-written "resolved" questions -- standing in for what
    # Step 9 (LangGraph) will eventually build automatically by merging
    # the original question with either a clarification answer or a
    # stated assumed_default from Step 4.
    test_questions = [  # fixed regression questions with known-correct answers in the seeded database
        "How many systems signed off last year?",  # unambiguous count query -> expected answer: 12
        "Who was the best customer last year, defined as highest revenue?",  # revenue definition -> expected: Acme Corp
        "Who was the best customer last year, defined as highest total profit?",  # profit definition -> expected: Stark Industries
        "What is the total sales in all years? Assume completed orders only, summed across all time.",  # aggregate with stated assumption -> expected: 3,077,465.82
    ]
    for q in test_questions:  # run each regression question in turn
        print_result(q)  # generate and print SQL for this question

    print("--- Type your own resolved questions below (type 'exit' to quit) ---")  # switch from fixed tests to interactive mode
    while True:  # keep asking until the user types exit
        user_question = input("Resolved question: ")  # read one question from the terminal
        if user_question == "exit":  # exit keyword ends the session
            break  # leave the loop
        print_result(user_question)  # generate and print SQL for the typed question
