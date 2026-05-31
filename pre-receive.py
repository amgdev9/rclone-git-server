#!/usr/bin/env python3
import sys

def main():
    for line in sys.stdin:
        old_rev, new_rev, ref_name = line.strip().split(' ')
        
        if ref_name == "refs/heads/main":
            print("--------------------------------------------------")
            print(" ERROR: Direct pushes to 'main' are disabled.    ")
            print(" Please push to a feature branch instead.        ")
            print("--------------------------------------------------")
            sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
