import json
import sys

from codec import decode


def handle(request):
    identity, labels = decode(request["record"])
    return {"id": identity, "labels": labels}


if __name__ == "__main__":
    for line in sys.stdin:
        print(json.dumps(handle(json.loads(line))))
