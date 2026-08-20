from studyai.content import generate_full_questions, generate_full_summary


class RecordingAI:
    def __init__(self, token_budget_boundary=100):
        self.boundary = token_budget_boundary
        self.summary_inputs = []
        self.question_inputs = []

    def count_tokens(self, text):
        return len(text)

    def summarize(self, text):
        self.summary_inputs.append(text)
        markers = [marker for marker in ("SEGMENT-A", "SEGMENT-B", "SEGMENT-C") if marker in text]
        return "NOTES:" + ",".join(markers)

    def generate_questions(self, text):
        self.question_inputs.append(text)
        markers = [marker for marker in ("SEGMENT-A", "SEGMENT-B", "SEGMENT-C") if marker in text]
        return "QUESTIONS:" + ",".join(markers)


def test_full_transcript_path_uses_complete_source_once():
    ai = RecordingAI()
    transcript = "SEGMENT-A SEGMENT-B SEGMENT-C"
    assert generate_full_summary(ai, transcript, [transcript], 1000).startswith("NOTES")
    assert ai.summary_inputs == [transcript]
    assert generate_full_questions(ai, transcript, [transcript], 1000).startswith("QUESTIONS")
    assert ai.question_inputs == [transcript]


def test_hierarchical_summary_includes_every_source_group():
    ai = RecordingAI()
    segments = ["SEGMENT-A " * 5, "SEGMENT-B " * 5, "SEGMENT-C " * 5]
    result = generate_full_summary(ai, " ".join(segments), segments, 150)
    all_inputs = " ".join(ai.summary_inputs)
    assert all(marker in all_inputs for marker in ("SEGMENT-A", "SEGMENT-B", "SEGMENT-C"))
    assert result.startswith("NOTES")


def test_hierarchical_questions_include_every_source_group():
    ai = RecordingAI()
    segments = ["SEGMENT-A " * 5, "SEGMENT-B " * 5, "SEGMENT-C " * 5]
    result = generate_full_questions(ai, " ".join(segments), segments, 150)
    all_inputs = " ".join(ai.question_inputs)
    assert all(marker in all_inputs for marker in ("SEGMENT-A", "SEGMENT-B", "SEGMENT-C"))
    assert result.startswith("QUESTIONS")
