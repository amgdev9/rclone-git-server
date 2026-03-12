#!/usr/bin/env python3
import sys

def main():
    for line in sys.stdin:
        old_rev, new_rev, ref_name = line.strip().split(' ')
        print(f"\n[Automation] Push detected on {ref_name}")
        print(f"[Automation] Moving from {old_rev} to {new_rev}")
        
        # Add your custom code here:
        # e.g., subprocess.run(["./deploy.sh"])
        
if __name__ == "__main__":
    main()
