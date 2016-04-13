"""
Generate and optionally draw precision and ROC curves.
"""
import os

import pandas

from themis import CsvFileType, to_csv
from themis.annotate import JudgmentFileType
from themis.curves import generate_curves, plot_curves
from themis.xmgr import FrequencyFileType


def plot_command(parser, subparsers):
    plot_parser = subparsers.add_parser("plot", help="generate performance plots from judged answers")
    plot_parser.add_argument("type", choices=["roc", "precision"], help="type of plot to create")
    plot_parser.add_argument("answers", type=CsvFileType(), nargs="+",
                             help="answers generated by one of the 'answer' commands")
    plot_parser.add_argument("--labels", nargs="+", help="names of the Q&A systems")
    plot_parser.add_argument("--frequency", required=True, type=FrequencyFileType(),
                             help="question frequency generated by the 'question frequency' command")
    plot_parser.add_argument("--judgments", required=True, nargs="+", type=JudgmentFileType(),
                             help="Q&A pair judgments generated by the 'judge interpret' command")
    plot_parser.add_argument("--output", default=".", help="output directory")
    plot_parser.add_argument("--draw", action="store_true", help="draw plots")
    plot_parser.set_defaults(func=CurvesHandlerClosure(parser))


def curves_handler(parser, args):
    if args.labels is None:
        args.labels = [answers.filename for answers in args.answers]
    elif not len(args.answers) == len(args.labels):
        parser.print_usage()
        parser.error("There must be a name for each plot.")
    labeled_qa_pairs = zip(args.labels, args.answers)
    judgments = pandas.concat(args.judgments)
    # noinspection PyTypeChecker
    curves = generate_curves(args.type, labeled_qa_pairs, judgments, args.frequency)
    # Write curves data.
    for label, data in curves.items():
        filename = os.path.join(args.output, "%s.%s.csv" % (args.type, label))
        to_csv(filename, data)
    # Optionally draw plot.
    if args.draw:
        plot_curves(curves)


class CurvesHandlerClosure(object):
    def __init__(self, parser):
        self.parser = parser

    def __call__(self, args):
        curves_handler(self.parser, args)