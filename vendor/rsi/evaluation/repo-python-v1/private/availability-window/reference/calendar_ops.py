def available(spans, window=None):
    if window is None:
        return sorted(spans)
    left, right = window
    clipped = ((max(start, left), min(end, right)) for start, end in spans)
    return sorted([start, end] for start, end in clipped if start < end)
