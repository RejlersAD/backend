"""CLI: python -m proposal_engine build <source>... --out <dir> [--quote Q-xxxxxx] [--reference rev0.pdf]"""
from __future__ import annotations

import argparse
import sys

from .docxkit import curate_template
from .pipeline import build_proposal


def main(argv=None):
    parser = argparse.ArgumentParser(prog='proposal_engine', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build', help='build master DOCX, client PDF binder and build_report.md')
    build.add_argument('sources', nargs='+', help='bid folders or ZIP files (e.g. 03_Proposal 04_Internal)')
    build.add_argument('--out', required=True)
    build.add_argument('--config', help='proposal standard YAML (default: Rejlers ADNOC standard)')
    build.add_argument('--quote', help='quote reference of the bid being produced, e.g. Q-102134')
    build.add_argument('--reference', help='baseline proposal PDF for structural comparison')
    build.add_argument('--converter', choices=('auto', 'office', 'libreoffice'), default='auto')
    build.add_argument('--no-render', action='store_true', help='scan, extract and validate only')
    curate = sub.add_parser('curate-template', help='make a body template from an approved company document')
    curate.add_argument('source')
    curate.add_argument('target')
    curate.add_argument('--token', action='append', default=[], metavar='OLD=NEW',
                        help='replace bid-specific text with a token, e.g. Q-101505={{quote_ref}}')
    args = parser.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if args.command == 'curate-template':
        curate_template(args.source, args.target, [tuple(item.split('=', 1)) for item in args.token])
        print(f'template written: {args.target}')
        return 0
    result = build_proposal(args.sources, args.out, config_path=args.config, quote_ref=args.quote,
                            reference_pdf=args.reference, converter_backend=args.converter,
                            render=not args.no_render)
    counts = result['counts']
    print(f"quote: {result['quote_ref']}  findings: {counts['ERROR']} ERROR / {counts['WARN']} WARN / {counts['INFO']} INFO")
    if result['comparison']:
        print(f"structural equivalence vs baseline: {result['comparison']['overall']:.1%}")
    print(f"report: {result['report']}")
    return 1 if counts['ERROR'] else 0


if __name__ == '__main__':
    sys.exit(main())
