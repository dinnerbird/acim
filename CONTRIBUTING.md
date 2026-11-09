# Contributing

Patches welcome, as pull requests against `main`. Two rules keep the
program running everywhere it is meant to run.

**The Python 2.5 subset.** `cim.py` runs unchanged on the Python that
shipped with Mac OS X Leopard (2.5) and on current 3.x. So: no `b''`
literals (use the `tob()` / `tos()` helpers), no `except X as e`
(`except X, e` is a syntax error on 3, so it is `except X:` plus
`sys.exc_info()`), no `str.format`, no set literals or dict
comprehensions, no `argparse` or `json` (the file has its own JSON
writer; `optparse` for options), no `collections.namedtuple`,
`os.path.relpath` or `bytearray`, no `print()` calls (use `write()`),
no f-strings, standard library only. The header of `cim.py` lists the helpers. Before a PR,
run `tools/contract_test.sh` on a Python 3 and, if you have one, on a
2.x (`PY=/path/to/python2.7 tools/contract_test.sh`). 47 checks, all
must pass; the card cannot tell this share from the Mac app and that is
the contract.

**The terminal.** The screen is curses and has to draw on a Tiger
Terminal, a VT100-class serial console, PuTTY, Windows conhost through
`windows-curses`, and a NAS over SSH with `LANG=C`. Use what curses
gives you: `curses.ACS_HLINE`, `ACS_VLINE`, the corners and tees, and
`curses.color_pair`. Do not put non-ASCII characters in strings that
reach `addstr`; UTF-8 box drawing turns into `â"€` or `?` on half of
those, while the ACS set goes through the terminal's own line-drawing
mode and curses falls back to `+ - |` by itself where there is none.
Keep `--plain` (the line-mode prompt) working for terminals with no
curses at all.

Smaller things: no em-dashes in text, plain prose in comments, say what
changed and why in the commit message. If you cannot test on an old Mac,
say so in the PR and someone with one will.

Releases are cut here with `tools/make_zip.sh` (the signed `CIM Text.app`
needs Rob's Developer ID; `SIGN=0` builds an ad-hoc one for local use)
and posted on a2cogs.com/download.
