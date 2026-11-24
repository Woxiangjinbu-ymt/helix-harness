def decode(record):
    return record.get("id"), record.get("labels", [])
