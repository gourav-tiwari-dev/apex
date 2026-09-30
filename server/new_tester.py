"""A new early-access tester's token (product, 30 Sep 2026).

    python server/new_tester.py sam@example.com

Prints a token for their server.json and the full TESTER_TOKENS value to paste into
`npx wrangler secret put TESTER_TOKENS`. testers.json (never committed) remembers who has which,
so a token can be taken back by deleting its line and putting the secret again.
"""
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTERS = os.path.join(HERE, "testers.json")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return
    who = sys.argv[1]
    testers = json.load(open(TESTERS)) if os.path.exists(TESTERS) else {}
    token = "apx_" + secrets.token_urlsafe(24)
    testers[who] = token
    with open(TESTERS, "w") as f:
        json.dump(testers, f, indent=1)
    print(f"token for {who}: {token}")
    print("TESTER_TOKENS =", ",".join(testers.values()))


if __name__ == "__main__":
    main()
