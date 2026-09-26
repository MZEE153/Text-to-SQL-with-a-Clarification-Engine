"""Step 10 helper: print what LangSmith recorded for this project, as a tree.

    python show_traces.py                 (the last 5 traces from the past 60 minutes)
    python show_traces.py --minutes 10 --limit 3

One TRACE = one top-level call (one `ask` or one `answer` process). Under it,
each LangGraph node is a child run, and each model call inside a node is a
grandchild -- with its latency and token counts. Nothing is shown that
LangSmith didn't actually store, so this is also a check that tracing works.
"""
import argparse  # standard library: command-line argument parsing
import os  # standard library: read the LANGSMITH_PROJECT environment variable
from collections import defaultdict  # a dict that creates an empty list for a missing key
from datetime import datetime, timedelta, timezone  # standard library: time arithmetic for "the last N minutes"

from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so LANGSMITH_API_KEY becomes available
from langsmith import Client  # the LangSmith SDK client (reads LANGSMITH_API_KEY from the environment)

load_dotenv()  # load .env now, before the client is created


def seconds(run) -> str:  # how long a run took, as text
    if run.start_time and run.end_time:  # both timestamps exist (a run still in progress has no end)
        return f"{(run.end_time - run.start_time).total_seconds():.2f}s"  # elapsed time
    return "running"  # no end time yet


def print_tree(run, children: dict, depth: int = 0) -> None:  # print one run, then its children indented beneath it
    tokens = f"  tokens={run.total_tokens}" if run.total_tokens else ""  # only model calls have token counts
    error = f"  ERROR: {str(run.error)[:80]}" if run.error else ""  # a failed run shows why
    print(f"{'    ' * depth}{run.name} [{run.run_type}] {run.status}  {seconds(run)}{tokens}{error}")  # e.g. "classify [chain] success  1.20s"
    for child in sorted(children.get(run.id, []), key=lambda r: r.start_time):  # children in the order they started
        print_tree(child, children, depth + 1)  # recurse one level deeper


def main() -> None:  # find recent traces and print each one
    parser = argparse.ArgumentParser(description="Show recent LangSmith traces")  # the argument parser
    parser.add_argument("--project", default=os.environ.get("LANGSMITH_PROJECT"), help="LangSmith project name (default: LANGSMITH_PROJECT)")  # which project to read
    parser.add_argument("--minutes", type=int, default=60, help="how far back to look")  # time window
    parser.add_argument("--limit", type=int, default=5, help="maximum number of traces")  # how many traces
    args = parser.parse_args()  # read sys.argv

    client = Client()  # connects lazily; fails with a clear auth error on the first call if the key is missing or wrong
    since = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)  # start of the window (LangSmith stores UTC)
    roots = list(client.list_runs(project_name=args.project, is_root=True, start_time=since, limit=args.limit))  # the top-level run of each recent trace
    if not roots:  # nothing found
        print(f"No traces in project '{args.project}' in the last {args.minutes} minutes.")  # say so plainly
        print("Uploads can take a few seconds to appear; a process that exits immediately can also lose its traces.")  # the two usual causes
        return  # nothing to print
    for root in sorted(roots, key=lambda r: r.start_time):  # oldest first, so the output reads in the order things happened
        run_list = list(client.list_runs(project_name=args.project, trace_id=root.trace_id))  # every run that belongs to this trace
        children = defaultdict(list)  # parent id -> its child runs
        for run in run_list:  # group the runs by their parent
            children[run.parent_run_id].append(run)  # a root run's parent id is None
        metadata = (root.extra or {}).get("metadata", {})  # metadata LangGraph attached to the run
        print(f"=== trace {str(root.trace_id)[:8]}  thread_id={metadata.get('thread_id')}  {len(run_list)} runs ===")  # header: which conversation, how many runs
        print_tree(root, children)  # the run tree
        print()  # blank line between traces


if __name__ == "__main__":  # only runs when executed directly
    main()  # go
