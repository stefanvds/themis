import json

import pandas

from themis import ANSWER, ANSWER_ID, TITLE, FILENAME, QUESTION, logger, CONFIDENCE, CsvFileType, IN_PURVIEW, CORRECT, \
    pretty_print_json

QUESTION_TEXT_INPUT = "QuestionText"  # Column header for input file required by Annotation Assist
QUESTION_TEXT_OUTPUT = "Question_Text"  # Columns header for output file created by Annotation Assist
IS_IN_PURVIEW = "Is_In_Purview"
SYSTEM_ANSWER = "System_Answer"
ANNOTATION_SCORE = "Annotation_Score"
TOP_ANSWER_TEXT = "TopAnswerText"
TOP_ANSWER_CONFIDENCE = "TopAnswerConfidence"
ANS_LONG = "ANS_LONG"
ANS_SHORT = "ANS_SHORT"
IS_ON_TOPIC = "IS_ON_TOPIC"


def annotation_assist_qa_input(answers, questions, judgments):
    """
    Create list of Q&A pairs for judgment by Annotation Assist.

    The Q&A pairs to be judged are compiled from sets of answers generated by Q&A systems. These may be filtered by an
    optional list of questions. Judgements may be taken from optional sets of previously judged Q&A pairs.

    :param answers: answers to questions as generated by Q&A systems
    :type answers: pandas.DataFrame
    :param questions: optional set of questions to filter on, if None use all answered questions
    :type questions: pandas.DataFrame
    :param judgments: optional judgments, look up a judgment here before sending the Q&A pair to Annotation Assist
    :type judgments: pandas.DataFrame
    :return: Q&A pairs to pass to Annotation Assist for judgment
    :rtype: pandas.DataFrame
    """
    qa_pairs = pandas.concat(answers)
    qa_pairs = qa_pairs.drop_duplicates([QUESTION, ANSWER])
    logger.info("%d Q&A pairs" % len(qa_pairs))
    if questions is not None:
        qa_pairs = pandas.merge(qa_pairs, questions)
        logger.info("%d Q&A pairs for %d unique questions" % (len(qa_pairs), len(questions)))
    if judgments:
        judged_qa_pairs = pandas.concat(judgments)
        assert not any(judged_qa_pairs.duplicated()), "There are Q&A pairs with multiple judgements"
        judged = pandas.merge(qa_pairs, judged_qa_pairs, on=(QUESTION, ANSWER))
        not_judged = qa_pairs[~qa_pairs[[QUESTION, ANSWER]].isin(judged[[QUESTION, ANSWER]])]
        logger.info("%d unjudged Q&A pairs" % len(not_judged))
    else:
        not_judged = qa_pairs
    not_judged = not_judged.rename(
        columns={QUESTION: QUESTION_TEXT_INPUT, ANSWER: TOP_ANSWER_TEXT, CONFIDENCE: TOP_ANSWER_CONFIDENCE})
    not_judged = not_judged[[QUESTION_TEXT_INPUT, TOP_ANSWER_TEXT, TOP_ANSWER_CONFIDENCE]]
    return not_judged


def create_annotation_assist_corpus(corpus):
    """
    Create the corpus file used by the Annotation Assist tool.

    :param corpus: corpus generated by 'xmgr corpus' command
    :type corpus: pandas.DataFrame
    :return: JSON representation of the corpus used by Annotation Assist
    :rtype: str
    """
    corpus["splitPauTitle"] = corpus[TITLE].apply(lambda title: title.split(":"))
    corpus = corpus.rename(columns={ANSWER: "text", ANSWER_ID: "pauId", TITLE: "title", FILENAME: "fileName"})
    return pretty_print_json(json.loads(corpus.to_json(orient="records"), encoding="utf-8"))


def interpret_annotation_assist(annotation_assist, judgment_threshold):
    """
    Convert the file produced by the Annotation Assist tool into a set of judgments that can be used by Themis.

    Convert the in purview column from an integer value to a boolean. Convert the annotation score column to a boolean
    correct column by applying a threshold. Drop any Q&A pairs that have multiple annotations.

    :param annotation_assist: Annotation Assist judgments
    :type annotation_assist: pandas.DataFrame
    :param judgment_threshold: threshold above which an answer is deemed correct
    :type judgment_threshold: pandas.DataFrame
    :return: Annotation Assist judgments with a boolean Correct column
    :rtype: pandas.DataFrame
    """
    qa_duplicates = annotation_assist[[QUESTION, ANSWER]].duplicated()
    if any(qa_duplicates):
        n = sum(qa_duplicates)
        logger.warning(
            "Dropping %d Q&A pairs with multiple annotations (%0.3f%%)" % (n, 100.0 * n / len(annotation_assist)))
        annotation_assist.drop_duplicates((QUESTION, ANSWER), keep=False, inplace=True)
    annotation_assist[IN_PURVIEW] = annotation_assist[IN_PURVIEW].astype("bool")
    annotation_assist[CORRECT] = annotation_assist[ANNOTATION_SCORE] >= judgment_threshold
    logger.info("Processed %d judgments" % len(annotation_assist))
    return annotation_assist.drop(ANNOTATION_SCORE, axis="columns").set_index([QUESTION, ANSWER])


def add_judgments_and_frequencies_to_qa_pairs(system_answers, judgments, question_frequencies):
    """
    Collate system answer confidences and annotator judgments by question/answer pair.
    Add to each pair the question frequency.

    Though you expect the set of question/answer pairs in the system answers and judgments to not be disjoint, it may
    be the case that neither is a subset of the other. If annotation is incomplete, there may be Q/A pairs in the
    system answers that haven't been annotated yet. If multiple systems are being judged, there may be Q/A pairs in the
    judgements that don't appear in the system answers.

    :param system_answers: question, answer, and confidence provided by a Q&A system
    :type system_answers: pandas.DataFrame
    :param judgments: question, answer, in purview, and judgement provided by annotators
    :type judgments: pandas.DataFrame
    :param question_frequencies: question and question frequency in the test set
    :type question_frequencies: pandas.DataFrame
    :return: question and answer pairs with confidence, in purview, judgement and question frequency
    :rtype: pandas.DataFrame
    """
    # The Annotation Assist tool strips newlines, so remove them from the answer text in the system output as well.
    system_answers[ANSWER] = system_answers[ANSWER].str.replace("\n", "")
    system_answers = pandas.merge(system_answers, judgments, on=(QUESTION, ANSWER))
    return pandas.merge(system_answers, question_frequencies, on=QUESTION)


class AnnotationAssistFileType(CsvFileType):
    """
    Read the file produced by the `Annotation Assist <https://github.com/cognitive-catalyst/annotation-assist>` tool.
    """

    def __init__(self):
        super(self.__class__, self).__init__([QUESTION_TEXT_OUTPUT, IS_IN_PURVIEW, SYSTEM_ANSWER, ANNOTATION_SCORE],
                                             {QUESTION_TEXT_OUTPUT: QUESTION, IS_IN_PURVIEW: IN_PURVIEW,
                                              SYSTEM_ANSWER: ANSWER})


class JudgmentFileType(CsvFileType):
    """
    Read the file produced by the 'judge interpret' command.
    """

    def __init__(self):
        super(self.__class__, self).__init__([QUESTION, ANSWER, IN_PURVIEW, CORRECT])
