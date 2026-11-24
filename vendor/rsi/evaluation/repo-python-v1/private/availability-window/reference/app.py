import json
import sys

from calendar_ops import available


def handle(request):
    return {"available": available(request["spans"], request.get("window"))}


if __name__ == "__main__":
    for line in sys.stdin:
        print(json.dumps(handle(json.loads(line))))
