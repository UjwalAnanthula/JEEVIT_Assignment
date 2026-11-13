# Orchestrates loading, classification, grouping and output generation.
import json
from pathlib import Path
from app.services.email_parser import load_inputs
from app.services.rule_classifier import classify
from app.services.deal_grouper import group_analyses
from app.services.recommendation import build_summary
def run(data_dir:Path, output_dir:Path):
    profile,emails=load_inputs(data_dir)
    analyses=[classify(e,profile) for e in emails]
    groups=group_analyses(analyses); output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"analyses.json").write_text(json.dumps([x.model_dump() for x in analyses],indent=2),encoding="utf-8")
    (output_dir/"deals.json").write_text(json.dumps(groups,indent=2),encoding="utf-8")
    (output_dir/"summary.md").write_text(build_summary(analyses),encoding="utf-8")
    return analyses,groups
