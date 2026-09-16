"""Step 4: given a natural-language question + the relevant schema, decide
whether it has one dominant interpretation (proceed straight to SQL) or
multiple ;ially different ones (ask the user to clarify first).
"""
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from schema_retrieval import retrieve_relevant_tables

load_dotenv()

SYSTEM_PROMPT = """You are the ambiguity-detection stage of a Text-to-SQL system.
You are NOT writing SQL. Your only job is to decide whether the user's
question, given the database schema below, has:
  - ONE dominant, low-risk interpretation -> not ambiguous, proceed to SQL, OR
  - MULTIPLE genuinely different interpretations that would produce
    different SQL and different answers -> ambiguous, ask the user to pick.

Only flag ambiguity when guessing wrong would give a materially different,
plausibly-wrong answer. Do not flag trivial or purely stylistic wording.
A common source of real ambiguity: subjective/superlative words like
"best", "top", "most active" that could map to  more than one metric.

If ambiguous, propose 2-5 concrete interpretations. Each interpretation
needs a short user-facing label and a precise one-sentence definition of
exactly what it means computationally, specific enough that someone could
write the SQL from it directly.

Database schema (relevant tables only):
{schema}
"""


class Interpretation(BaseModel):
    label: str = Field(description="Short label shown to the user, e.g. 'Highest revenue'")
    detail: str = Field(
        description="Precise one-sentence definition of this interpretation, specific "
        "enough to write SQL from directly, e.g. 'The customer whose completed orders "
        "sum to the highest total_amount in the given period.'"
    )


class AmbiguityCheck(BaseModel):
    is_ambiguous: bool = Field(
        description="True only if there are multiple materially different valid "
        "interpretations that would change the SQL and the answer."
    )
    reasoning: str = Field(description="One or two sentences explaining the decision.")
    interpretations: list[Interpretation] = Field(
        default_factory=list,
        description="2-5 distinct interpretations if ambiguous; empty list if not.",
    )


model = ChatGoogleGenerativeAI(model="gemini-3.6-flash")
classifier = model.with_structured_output(AmbiguityCheck)


def check_ambiguity(question: str) -> AmbiguityCheck:
    # top_k=4 with only 4 tables in the schema means "no real filtering" --
    # every table comes back every time. That's deliberate: it keeps this
    # step using the same retrieve_relevant_tables() path Step 5 will use,
    # instead of a special-cased "just list everything" bypass. As the
    # schema grows past 4 tables, this same call starts filtering for real
    # with no code change needed.
    relevant = retrieve_relevant_tables(question, top_k=4)
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)
    system = SYSTEM_PROMPT.format(schema=schema_text)
    return classifier.invoke([("system", system), ("human", question)])


def print_result(question: str) -> None:
    print(f"Question: {question}")
    result = check_ambiguity(question)
    print(f"  is_ambiguous: {result.is_ambiguous}")
    print(f"  reasoning: {result.reasoning}")
    if result.is_ambiguous:
        for opt in result.interpretations:
            print(f"    - {opt.label}: {opt.detail}")
    print()


if __name__ == "__main__":
    # Known regression cases -- run these first every time.
    test_questions = [
        "How many systems signed off last year?",
        "Who was the best customer last year?",
        "What products have the highest profit margin?",
        "what is the name of the computer?",
    ]
    for question in test_questions:
        print_result(question)

    # Then drop into an interactive loop for your own questions.
    print("--- Type your own questions below (type 'exit' to quit) ---")
    while True:
        user_question = input("Your question: ")
        if user_question == "exit":
            break
        print_result(user_question)
