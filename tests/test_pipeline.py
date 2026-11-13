# Checks that the complete sample inbox produces all expected output artifacts.
from pathlib import Path
from app.pipeline import run
def test_sample_inbox(tmp_path):
    items,groups=run(Path("data"),tmp_path)
    assert len(items)==15
    assert all((tmp_path/name).exists() for name in ["analyses.json","deals.json","summary.md"])
