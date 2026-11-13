# Provides the command-line entry point for processing the creator inbox.
import argparse
from pathlib import Path
from app.pipeline import run
def main():
    parser=argparse.ArgumentParser(description="JEEVIT Brand Deal Inbox Assistant")
    parser.add_argument("--data",type=Path,default=Path("data")); parser.add_argument("--output",type=Path,default=Path("output"))
    args=parser.parse_args(); analyses,groups=run(args.data,args.output)
    print(f"Processed {len(analyses)} emails into {len(groups)} brand groups.")
    print(f"Outputs: {args.output.resolve()}")
if __name__=="__main__": main()
