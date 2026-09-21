"""Step 5, Ollama variant: same job as sql_generator.py, backed by a local
qwen2.5:7b-instruct model instead of a hosted API -- the point is to stop
depending on free-tier request quotas entirely (this session hit Gemini's
20/day cap, then two separate HF Inference Providers dead ends, before
landing here).

Unlike the HF routed-inference attempt, Ollama models generally DO support
tool-calling through langchain-ollama's ChatOllama, so with_structured_output
should work the same way it does for Gemini/Groq -- this script is the
first real test of that for a locally-run model.

Requires the Ollama app running locally (default http://localhost:11434)
and the model already pulled: `ollama pull qwen2.5:7b-instruct`.
"""
from langchain_ollama import ChatOllama

from sql_generator import SYSTEM_PROMPT, SQLGenerationResult
from schema_retrieval import retrieve_relevant_tables

model = ChatOllama(model="qwen2.5:7b-instruct", temperature=0.1)
generator = model.with_structured_output(SQLGenerationResult)


def generate_sql(resolved_question: str) -> SQLGenerationResult:
    relevant = retrieve_relevant_tables(resolved_question, top_k=4)
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)
    system = SYSTEM_PROMPT.format(schema=schema_text)
    return generator.invoke([("system", system), ("human", resolved_question)])


def print_result(resolved_question: str) -> None:
    print(f"Resolved question: {resolved_question}")
    result = generate_sql(resolved_question)
    print(f"  tables_used: {result.tables_used}")
    print(f"  explanation: {result.explanation}")
    print(f"  sql:\n{result.sql}")
    print()


if __name__ == "__main__":
    test_questions = [
        "How many systems signed off last year?",
        "Who was the best customer last year, defined as highest revenue?",
        "Who was the best customer last year, defined as highest total profit?",
        "What is the total sales in all years? Assume completed orders only, summed across all time.",
    ]
    for q in test_questions:
        print_result(q)
