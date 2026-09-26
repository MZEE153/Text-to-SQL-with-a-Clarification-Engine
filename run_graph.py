"""Command-line entry point for the Text-to-SQL pipeline.

    python run_graph.py ask "Who was the best customer last year?"
    python run_graph.py answer --thread <id> 1                       (pick option number 1)
    python run_graph.py answer --thread <id> "my own definition"     (or type your own)

Each command is a separate process. When the pipeline needs a clarification,
`ask` prints the options and EXITS; the paused state lives in a SQLite file, so
`answer` (a different process, possibly much later) picks up exactly where it
stopped without repeating the classification.
"""
import argparse  # standard library: command-line argument parsing
import uuid  # standard library: generate a unique thread id when none is given
from pathlib import Path  # standard library: path handling

from langgraph.checkpoint.sqlite import SqliteSaver  # the on-disk checkpointer: saves the graph's state after every node
from langgraph.types import Command  # how a paused graph is resumed with the user's answer

from txt2sql_graph import build_graph  # the Step 9 workflow

DEFAULT_DB = Path(__file__).resolve().parent / "checkpoints.sqlite"  # where paused conversations are saved (git-ignored)


def show(graph, thread: str) -> None:  # print whatever state the thread is in: waiting for the user, or finished
    state = graph.get_state({"configurable": {"thread_id": thread}})  # load the saved snapshot for this conversation
    print(f"THREAD: {thread}")  # always show the id, so the user can resume it
    if state.interrupts:  # the graph is paused, waiting for a clarification (state.next is unreliable after a re-pause, so we check interrupts)
        payload = state.tasks[0].interrupts[-1].value  # what ask_user handed over when it paused
        print("CLARIFICATION NEEDED")  # a marker that scripts can look for
        print(f"  Question: {payload['question']}")  # the original question
        print(f"  Why: {payload['reasoning']}")  # why it is ambiguous
        if "error" in payload:  # the previous answer was not usable
            print(f"  !! {payload['error']}")  # say why
        for i, option in enumerate(payload["options"]):  # one line per candidate interpretation
            print(f"  [{i}] {option['label']}: {option['detail']}")  # e.g. "[0] Highest revenue: The customer whose ..."
        print(f'  Reply with:  python run_graph.py answer --thread {thread} <number or your own definition in quotes>')  # the exact command to continue
        return  # nothing more to show until the user answers
    values = state.values  # the finished state
    print("ANSWER:")  # a marker that scripts can look for
    print(values.get("answer", "(no answer was produced)"))  # the final text, including any assumption / cut-off / warning lines
    if values.get("sql"):  # a query was generated
        print(f"SQL used: {' '.join(values['sql'].split())}")  # show it for transparency, collapsed onto one line


def main() -> None:  # parse the command line and run the requested action
    parser = argparse.ArgumentParser(description="Text-to-SQL pipeline")  # the top-level parser
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file holding paused conversations")  # lets tests use a throwaway file
    sub = parser.add_subparsers(dest="command", required=True)  # two sub-commands
    ask = sub.add_parser("ask", help="ask a new question")  # sub-command 1
    ask.add_argument("question")  # the question text
    ask.add_argument("--thread", default=None, help="conversation id (default: a new random one)")  # optional explicit id
    answer = sub.add_parser("answer", help="answer a pending clarification")  # sub-command 2
    answer.add_argument("--thread", required=True, help="the conversation id printed by `ask`")  # which conversation to resume
    answer.add_argument("choice", help="an option number, or your own definition in quotes")  # the user's reply
    args = parser.parse_args()  # read sys.argv

    with SqliteSaver.from_conn_string(args.db) as saver:  # open (or create) the checkpoint file
        graph = build_graph(saver)  # build the workflow on top of it
        if args.command == "ask":  # a new question
            thread = args.thread or uuid.uuid4().hex[:8]  # a short unique id
            graph.invoke({"question": args.question}, {"configurable": {"thread_id": thread}})  # run until it finishes or pauses
        else:  # resuming a paused conversation
            thread = args.thread  # the id given by the user
            if not graph.get_state({"configurable": {"thread_id": thread}}).interrupts:  # nothing is waiting on this thread
                print(f"Nothing is waiting for an answer on thread {thread}.")  # tell the user plainly
                raise SystemExit(1)  # non-zero exit code
            choice = int(args.choice) if args.choice.strip().isdigit() else args.choice  # a bare number picks an option; anything else is free text
            graph.invoke(Command(resume=choice), {"configurable": {"thread_id": thread}})  # resume from the pause
        show(graph, thread)  # print the outcome


if __name__ == "__main__":  # only runs when executed directly
    main()  # go
