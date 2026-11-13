# Loads and validates the supplied creator profile and inbox JSON files.
import json
from pathlib import Path
from app.models.schemas import EmailRecord
def load_inputs(data_dir: Path):
    profile=json.loads((data_dir/"creator_profile.json").read_text(encoding="utf-8"))
    raw=json.loads((data_dir/"inbox.json").read_text(encoding="utf-8"))
    return profile,[EmailRecord.model_validate(x) for x in raw]
