#!/usr/bin/env python3
import sys

def main():
    # Git passes info via stdin: <old-rev> <new-rev> <ref-name>
    for line in sys.stdin:
        old_rev, new_rev, ref_name = line.strip().split(' ')
        
        # Check if the user is trying to push to the 'main' branch
        if ref_name == "refs/heads/main":
            print("--------------------------------------------------")
            print(" ERROR: Direct pushes to 'main' are disabled.    ")
            print(" Please push to a feature branch instead.        ")
            print("--------------------------------------------------")
            sys.exit(1) # This rejects the push

    sys.exit(0) # This allows the push for other branches

if __name__ == "__main__":
    main()
