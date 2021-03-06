import functools
import itertools
import math, os

import pandas
import numpy as np
from bs4 import BeautifulSoup
from nltk import word_tokenize, FreqDist

from themis import CsvFileType, QUESTION, ANSWER, CONFIDENCE, IN_PURVIEW, CORRECT, FREQUENCY, logger, ANSWER_ID

SYSTEM = "System"
ANSWERING_SYSTEM = "Answering System"


def corpus_statistics(corpus):
    """
    Generate statistics for the corpus.

    :param corpus: corpus generated by 'xmgr corpus' command
    :type corpus: pandas.DataFrame
    :return: answers in corpus, tokens in the corpus, histogram of answer length in tokens
    :rtype: (int, int, dict(int, int))
    """
    answers = len(corpus)
    token_frequency = FreqDist([len(word_tokenize(BeautifulSoup(answer, "lxml").text)) for answer in corpus[ANSWER]])
    histogram = {}
    for frequency, count in token_frequency.items():
        histogram[frequency] = histogram.get(frequency, 0) + count
    tokens = sum(token_frequency.keys())
    n = sum(corpus.duplicated(ANSWER_ID))
    if n:
        logger.warning("%d duplicated answer IDs (%0.3f%%)" % (n, 100.0 * n / answers))
    return answers, tokens, histogram


def truth_statistics(truth):
    """
    Generate statistics for the truth.

    :param truth: question to answer mapping used in training
    :type truth: pandas.DataFrame
    :return: number of training pairs, number of unique questions, number of unique answers, histogram of number of
            questions per answer
    :rtype: (int, int, int, pandas.DataFrame)
    """
    pairs = len(truth)
    questions = len(truth[QUESTION].unique())
    answers = len(truth[ANSWER_ID].unique())
    question_histogram = truth[[ANSWER_ID, QUESTION]].groupby(ANSWER_ID).count()
    return pairs, questions, answers, question_histogram


def system_similarity(systems_data):
    """
    For each system pair, return the number of questions they answered the same.

    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :return: table of pairs of systems and their similarity statistics
    :rtype: pandas.DataFrame
    """
    systems_data = drop_missing(systems_data)
    systems = systems_data[SYSTEM].drop_duplicates().sort_values()
    columns = ["System 1", "System 2", "Same Answer", "Same Answer %"]
    results = pandas.DataFrame(columns=columns)
    for x, y in itertools.combinations(systems, 2):
        data_x = systems_data[systems_data[SYSTEM] == x]
        data_y = systems_data[systems_data[SYSTEM] == y]
        m = pandas.merge(data_x, data_y, on=QUESTION)
        n = len(m)
        logger.info("%d question/answer pairs in common for %s and %s" % (n, x, y))
        same_answer = sum(m["%s_x" % ANSWER] == m["%s_y" % ANSWER])
        same_answer_pct = 100.0 * same_answer / n
        results = results.append(
            pandas.DataFrame([[x, y, same_answer, same_answer_pct]], columns=columns))
    results["Same Answer"] = results["Same Answer"].astype("int64")
    return results.set_index(["System 1", "System 2"])


def compare_systems(systems_data, x, y, comparison_type):
    """
    On which questions did system x do better or worse than system y?

    System x did better than system y if it correctly answered a question when system y did not, and vice versa.

    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :param x: system name
    :type x: str
    :param y: system name
    :type y: str
    :param comparison_type: "better" or "worse"
    :type comparison_type: str
    :return: all question/answer pairs from system x that were either better or worse than system y
    :rtype: pandas.DataFrame
    """

    def col_name(type, system):
        return type + " " + system

    systems_data = drop_missing(systems_data)
    systems_data = systems_data[systems_data[IN_PURVIEW]]
    data_x = systems_data[systems_data[SYSTEM] == x]
    data_y = systems_data[systems_data[SYSTEM] == y][[QUESTION, ANSWER, CONFIDENCE, CORRECT]]
    questions = pandas.merge(data_x, data_y, on=QUESTION, how="left", suffixes=(" " + x, " " + y)).dropna()
    n = len(questions)
    logger.info("%d shared question/answer pairs between %s and %s" % (n, x, y))
    x_correct = col_name(CORRECT, x)
    y_correct = col_name(CORRECT, y)
    if comparison_type == "better":
        a = questions[x_correct] == True
        b = questions[y_correct] == False
    elif comparison_type == "worse":
        a = questions[x_correct] == False
        b = questions[y_correct] == True
    else:
        raise ValueError("Invalid comparison type %s" % comparison_type)
    d = questions[a & b]
    m = len(d)
    logger.info("%d %s (%0.3f%%)" % (m, comparison_type, 100.0 * m / n))
    d = d[[QUESTION, FREQUENCY,
           col_name(ANSWER, x), col_name(CONFIDENCE, x), col_name(ANSWER, y), col_name(CONFIDENCE, y)]]
    d = d.sort_values([col_name(CONFIDENCE, x), FREQUENCY, QUESTION], ascending=(False, False, True))
    return d.set_index(QUESTION)


def analyze_answers(systems_data, freq_le, freq_gr):
    """
    Statistics about all the answered questions in a test set broken down by system.

    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :param freq_gr: optionally only consider questions with frequency greater than this
    :type freq_gr: int
    :param freq_le: optionally only consider questions with frequency less than or equal to this
    :type freq_le: int
    :return: answer summary statistics
    :rtype: pandas.DataFrame
    """
    total = "Total"
    in_purview_percent = IN_PURVIEW + " %"
    correct_percent = CORRECT + " %"
    unique = "Unique"
    systems_data = pandas.concat(systems_data).dropna()
    if freq_le is not None:
        systems_data = systems_data[systems_data[FREQUENCY] <= freq_le]
    if freq_gr is not None:
        systems_data = systems_data[systems_data[FREQUENCY] > freq_gr]
    systems = systems_data.groupby(SYSTEM)
    summary = systems[[IN_PURVIEW, CORRECT]].sum()
    summary[[IN_PURVIEW, CORRECT]] = summary[[IN_PURVIEW, CORRECT]].astype("int")
    summary[total] = systems.count()[QUESTION]
    summary[unique] = systems[ANSWER].nunique()
    summary[in_purview_percent] = summary[IN_PURVIEW] / summary[total] * 100.0
    summary[correct_percent] = summary[CORRECT] / summary[IN_PURVIEW] * 100.0
    return summary.sort_values(correct_percent, ascending=False)[
        [total, unique, IN_PURVIEW, in_purview_percent, CORRECT, correct_percent]]


def truth_coverage(corpus, truth, systems_data):
    """
    Statistics about which answers came from the truth set broken down by system.

    :param corpus: corpus generated by 'xmgr corpus' command
    :type corpus: pandas.DataFrame
    :param truth: question to answer mapping used in training
    :type truth: pandas.DataFrame
    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :return: truth coverage summary statistics
    :rtype: pandas.DataFrame
    """
    truth_answers = pandas.merge(corpus, truth, on=ANSWER_ID)[ANSWER].drop_duplicates()
    n = len(corpus)
    m = len(truth_answers)
    logger.info("%d answers out of %d possible answers in truth (%0.3f%%)" % (m, n, 100.0 * m / n))
    systems_data = pandas.concat(systems_data).dropna()
    answers = systems_data.groupby(SYSTEM)[[CORRECT]].count()
    answers_in_truth = systems_data[systems_data[ANSWER].isin(truth_answers)].groupby(SYSTEM)[[ANSWER]]
    summary = answers_in_truth.count()
    summary["Answers"] = answers
    summary = summary.rename(columns={ANSWER: "Answers in Truth"})
    summary["Answers in Truth %"] = 100 * summary["Answers in Truth"] / summary["Answers"]
    correct_answers = systems_data[systems_data[CORRECT]]
    correct_answers_in_truth = correct_answers[correct_answers[ANSWER].isin(truth_answers)]
    summary["Correct Answers"] = correct_answers.groupby(SYSTEM)[CORRECT].count()
    summary["Correct Answers in Truth"] = correct_answers_in_truth.groupby(SYSTEM)[CORRECT].count()
    summary["Correct Answers in Truth %"] = 100 * summary["Correct Answers in Truth"] / summary["Correct Answers"]
    return summary[
        ["Answers", "Correct Answers",
         "Answers in Truth", "Answers in Truth %",
         "Correct Answers in Truth", "Correct Answers in Truth %"]].sort_values("Correct Answers", ascending=False)


# noinspection PyTypeChecker
def long_tail_fat_head(frequency_cutoff, systems_data):
    """
    Accuracy statistics broken down by question "fat head" and "long tail".

    The fat head is defined to be all questions with a frequency above a cutoff value. The long tail is defined to be
    all questions with a frequency below that value.

    :param frequency_cutoff: question frequency dividing fat head from long tail
    :type frequency_cutoff: int
    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :return: truth coverage summary statistics for the fat head and long tail
    :rtype: (pandas.DataFrame, pandas.DataFrame)
    """
    fat_head = analyze_answers(systems_data, None, frequency_cutoff)
    long_tail = analyze_answers(systems_data, frequency_cutoff, None)
    return fat_head, long_tail


def in_purview_disagreement(systems_data):
    """
    Return collated data where in-purview judgments are not unanimous for a question.

    These questions' purview should be rejudged to make them consistent.

    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :return: subset of collated data where the purview judgments are not unanimous for a question
    :rtype: pandas.DataFrame
    """
    question_groups = systems_data[[QUESTION, IN_PURVIEW]].groupby(QUESTION)
    index = question_groups.filter(lambda qg: len(qg[IN_PURVIEW].unique()) == 2).index
    purview_disagreement = systems_data.loc[index]
    m = len(purview_disagreement[QUESTION].drop_duplicates())
    if m:
        n = len(systems_data[QUESTION].drop_duplicates())
        logger.warning("%d out of %d questions have non-unanimous in-purview judgments (%0.3f%%)"
                       % (m, n, 100.0 * m / n))
    return purview_disagreement


def oracle_combination(systems_data, system_names, oracle_name):
    """
    Combine results from multiple systems into a single oracle system. The oracle system gets a question correct if any
    of its component systems did. If the answer is correct use the highest confidence. If it is incorrect, use the
    lowest confidence.

    (A question is in purview if judgments from all the systems say it is in purview. These judgments should be
    unanimous. The 'themis analyze purview' command finds when this is not the case.)

    :param systems_data: collated results for all systems
    :type systems_data: pandas.DataFrame
    :param system_names: names of systems to combine
    :type system_names: list of str
    :param oracle_name: the name of the combined system
    :type oracle_name: str
    :return: oracle results in collated format
    :rtype: pandas.DataFrame
    """

    def log_correct(system_data, name):
        n = len(system_data)
        m = sum(system_data[CORRECT])
        logger.info("%d of %d correct in %s (%0.3f%%)" % (m, n, name, 100.0 * m / n))

    percentile = "Percentile"
    systems_data = drop_missing(systems_data)
    # Extract the systems of interest and map confidences to percentile rank.
    systems = []
    for system_name in system_names:
        system = systems_data[systems_data[SYSTEM] == system_name].set_index(QUESTION)
        system[percentile] = system[CONFIDENCE].rank(pct=True)
        log_correct(system, system_name)
        systems.append(system)
    # Get the questions asked to all the systems.
    questions = functools.reduce(lambda m, i: m.intersection(i), (system.index for system in systems))
    # Start the oracle with a copy of one of the systems.
    oracle = systems[0].loc[questions].copy()
    oracle = oracle.drop([ANSWER, percentile], axis="columns")
    oracle[SYSTEM] = oracle_name
    # An oracle question is in purview if all systems mark it as in purview. There should be consensus on this.
    systems_in_purview = [system.loc[questions][[IN_PURVIEW]] for system in systems]
    oracle[[IN_PURVIEW]] = functools.reduce(lambda m, x: m & x, systems_in_purview)
    # An oracle question is correct if any system gets it right.
    systems_correct = [system.loc[questions][[CORRECT]] for system in systems]
    oracle[[CORRECT]] = functools.reduce(lambda m, x: m | x, systems_correct)
    # If the oracle answer is correct, use the highest confidence.
    confidences = [system[[percentile]].rename(columns={percentile: system[SYSTEM][0]}) for system in systems]
    system_confidences = functools.reduce(lambda m, x: pandas.merge(m, x, left_index=True, right_index=True),
                                          confidences)
    correct = oracle[CORRECT].astype("bool")
    oracle.loc[correct, CONFIDENCE] = system_confidences[correct].max(axis=1)
    oracle.loc[correct, ANSWERING_SYSTEM] = system_confidences[correct].idxmax(axis=1)
    # If the question is out of purview or the answer is incorrect, use the lowest confidence.
    oracle.loc[~correct, CONFIDENCE] = system_confidences[~correct].min(axis=1)
    oracle.loc[~correct, ANSWERING_SYSTEM] = system_confidences[~correct].idxmin(axis=1)
    # Use the answer produced by the system incorporated into the oracle.
    oracle = oracle.reset_index()
    oracle[ANSWER] = pandas.merge(systems_data, oracle,
                                  left_on=[QUESTION, SYSTEM], right_on=[QUESTION, ANSWERING_SYSTEM])[ANSWER]
    log_correct(oracle, oracle_name)
    return oracle


def filter_judged_answers(systems_data, correct, system_names):
    """
    Filter out just the correct or incorrect in-purview answers.

    :param systems_data: questions, answers, and judgments across systems
    :type systems_data: list of pandas.DataFrame
    :param correct: filter correct or incorrect answers?
    :type correct: bool
    :param system_names: systems to filter to, if None show all systems
    :type system_names: list of str
    :return: set of in-purview questions with answers judged either correct or incorrect
    :rtype: pandas.DataFrame
    """
    systems_data = pandas.concat(systems_data).dropna()
    if system_names is not None:
        systems_data = systems_data[systems_data[SYSTEM].isin(system_names)]
    filtered = systems_data[(systems_data[IN_PURVIEW] == True) & (systems_data[CORRECT] == correct)]
    n = len(systems_data)
    m = len(filtered)
    logger.info("%d in-purview %s answers out of %d (%0.3f%%)" %
                (m, {True: "correct", False: "incorrect"}[correct], n, 100 * m / n))
    return filtered


def add_judgments_and_frequencies_to_qa_pairs(qa_pairs, judgments, question_frequencies, remove_newlines):
    """
    Collate system answer confidences and annotator judgments by question/answer pair.
    Add to each pair the question frequency. Collated system files are used as input to subsequent cross-system
    analyses.

    Though you expect the set of question/answer pairs in the system answers and judgments to not be disjoint, it may
    be the case that neither is a subset of the other. If annotation is incomplete, there may be Q/A pairs in the
    system answers that haven't been annotated yet. If multiple systems are being judged, there may be Q/A pairs in the
    judgements that don't appear in the system answers.

    Some versions of Annotation Assist strip newlines from the answers they return in the judgement files, so
    optionally take this into account when joining on question/answer pairs.

    :param qa_pairs: question, answer, and confidence provided by a Q&A system
    :type qa_pairs: pandas.DataFrame
    :param judgments: question, answer, in purview, and judgement provided by annotators
    :type judgments: pandas.DataFrame
    :param question_frequencies: question and question frequency in the test set
    :type question_frequencies: pandas.DataFrame
    :param remove_newlines: join judgments on answers with newlines removed
    :type remove_newlines: bool
    :return: question and answer pairs with confidence, in purview, judgement and question frequency
    :rtype: pandas.DataFrame
    """
    qa_pairs = pandas.merge(qa_pairs, question_frequencies, on=QUESTION, how="left")
    if remove_newlines:
        qa_pairs["Temp"] = qa_pairs[ANSWER].str.replace("\n", "")
        qa_pairs = qa_pairs.rename(columns={"Temp": ANSWER, ANSWER: "Temp"})
    qa_pairs = pandas.merge(qa_pairs, judgments, on=(QUESTION, ANSWER), how="left")
    if remove_newlines:
        del qa_pairs[ANSWER]
        qa_pairs = qa_pairs.rename(columns={"Temp": ANSWER})
    return qa_pairs


def drop_missing(systems_data):
    if any(systems_data.isnull()):
        n = len(systems_data)
        systems_data = systems_data.dropna()
        m = n - len(systems_data)
        if m:
            logger.warning("Dropping %d of %d question/answer pairs missing information (%0.3f%%)" %
                           (m, n, 100.0 * m / n))
    return systems_data


def kfold_split(df, outdir, _folds = 5):
    # Randomize the order of the input dataframe
    df = df.iloc[np.random.permutation(len(df))]
    df = df.reset_index(drop=True)
    foldSize = int(math.ceil(len(df) / float(_folds)))
    logger.info("Total records: " + str(len(df)))
    logger.info("Fold size: " + str(foldSize))

    for x in range(0, _folds):
        fold_low = x*foldSize
        fold_high = (x+1)*foldSize

        if fold_high >= len(df):
            fold_high = len(df)

        test_df = df.iloc[fold_low:fold_high]
        train_df = df.drop(df.index[fold_low:fold_high])

        test_df.to_csv(os.path.join(outdir, 'Test' + str(x) + '.csv'), encoding='utf-8', index=False)
        train_df.to_csv(os.path.join(outdir, 'Train' + str(x) + '.csv'), header=False, encoding='utf-8', index=False)

        logger.info("--- Train_Fold_" + str(x) + ' size = ' + str(len(train_df)))
        logger.info("--- Test_Fold_" + str(x) + ' size = ' + str(len(test_df)))


class CollatedFileType(CsvFileType):
    columns = [QUESTION, SYSTEM, ANSWER, CONFIDENCE, IN_PURVIEW, CORRECT, FREQUENCY]

    def __init__(self):
        super(self.__class__, self).__init__(self.__class__.columns)

    def __call__(self, filename):
        collated = super(self.__class__, self).__call__(filename)
        m = sum(collated[collated[IN_PURVIEW] == False][CORRECT])
        if m:
            n = len(collated)
            logger.warning(
                "%d out of %d question/answer pairs in %s are marked as out of purview but correct (%0.3f%%)"
                % (m, n, filename, 100.0 * m / n))
        return collated

    @classmethod
    def output_format(cls, collated):
        collated = collated[cls.columns]
        collated = collated.sort_values([QUESTION, SYSTEM])
        return collated.set_index([QUESTION, SYSTEM, ANSWER])


class OracleFileType(CollatedFileType):
    columns = CollatedFileType.columns[:2] + [ANSWERING_SYSTEM] + CollatedFileType.columns[2:]
