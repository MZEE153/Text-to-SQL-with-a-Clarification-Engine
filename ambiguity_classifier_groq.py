"""Step 4, Groq variant: same job as ambiguity_classifier.py (classify a
question as clear / default-with-assumption / genuinely ambiguous), backed
by Groq so the whole pipeline can run on one provider and not depend on
Gemini's 20-requests/day free-tier cap.

Reuses the SAME prompt and AmbiguityCheck schema as the Gemini version --
only the model differs. NOTE: that prompt's calibration was tuned by hand on 
Gemini; a different model can read the same words differently, so this
variant's behaviour has to be checked, not assumed (see the regression run
below).
"""
from datetime import date  # standard library: today's date (the model cannot know it, and guessed 2023 for "last year" without it)
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so GROQ_API_KEY becomes available
from langchain_groq import ChatGroq  # LangChain's wrapper around Groq's hosted chat models

from ambiguity_classifier import SYSTEM_PROMPT, AmbiguityCheck  # reuse the SAME prompt and result schema as the Gemini version -- only the model differs
from business_rules import BUSINESS_RULES  # domain rules shared with the SQL generator (cancelled orders are not sales)
from schema_retrieval import retrieve_relevant_tables  # Step 3b: embedding search returning the tables relevant to a question

load_dotenv()  # load .env now, before the Groq client is created below

model = ChatGroq(model="openai/gpt-oss-20b", temperature=0)  # Groq client; temperature 0 so the same question gets the same classification as far as possible
classifier = model.with_structured_output(AmbiguityCheck)  # wraps the model so its reply is parsed into an AmbiguityCheck object (tool-calling under the hood)


def check_ambiguity(question: str) -> AmbiguityCheck:  # main entry point: raw user question in, typed classification out
    relevant = retrieve_relevant_tables(question, top_k=4)  # embedding search; returns all 4 tables today, filters for real if the schema grows
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)  # keep only each table's description text (drop name and score), blank-line separated
    today_line = f"Today's date is {date.today().isoformat()}. Use it to turn relative dates such as 'last year' into explicit calendar years; never guess the current year.\n\n"  # models have no clock: without this, "last year" was resolved to 2023
    system = today_line + BUSINESS_RULES + SYSTEM_PROMPT.format(schema=schema_text)  # today's date, then the shared business rules, then the shared prompt with its {schema} placeholder filled in
    return classifier.invoke([("system", system), ("human", question)])  # call Groq with system + user messages; returns an AmbiguityCheck


def print_result(question: str) -> None:  # helper: classify a question and pretty-print the outcome for manual testing
    print(f"Question: {question}")  # echo the input question
    result = check_ambiguity(question)  # run the classifier
    print(f"  is_ambiguous: {result.is_ambiguous}")  # the branch flag
    print(f"  reasoning: {result.reasoning}")  # the model's justification
    if result.is_ambiguous:  # genuinely ambiguous: the user should be asked to choose
        for opt in result.interpretations:  # list each candidate interpretation
            print(f"    - {opt.label}: {opt.detail}")  # label and its precise definition
    elif result.assumed_default:  # proceeding, but a default was assumed
        print(f"  assumed_default: {result.assumed_default}")  # show the assumption for transparency
    print()  # blank line between results


if __name__ == "__main__":  # only runs when executed directly, not when imported
    for q in [  # the Step 4 regression questions, plus the "total sales" default case
        "How many systems signed off last year?",  # clear -> expected: not ambiguous
        "Who was the best customer last year?",  # "best" -> expected: ambiguous (revenue / order count / profit)
        "What products have the highest profit margin?",  # expected: not ambiguous
        "What is the total sales in all years?",  # expected: not ambiguous, with a stated default
        "what is the name of the computer?",  # known open gap: doesn't map cleanly to the schema
    ]:
        print_result(q)  # classify and print
