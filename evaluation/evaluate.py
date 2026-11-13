# Measures category accuracy against the manually authored expected labels.
import json
from pathlib import Path
from app.pipeline import run
if __name__=="__main__":
    results,_=run(Path("data"),Path("output")); expected=json.loads(Path("evaluation/expected_results.json").read_text())
    correct=sum(x.category==expected[x.email_id] for x in results)
    print(f"Category accuracy: {correct}/{len(expected)} ({correct/len(expected):.1%})")
    for x in results:
        if x.category!=expected[x.email_id]: print(f"{x.email_id}: expected {expected[x.email_id]}, got {x.category}")
