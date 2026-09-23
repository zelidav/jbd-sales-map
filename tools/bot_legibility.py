#!/usr/bin/env python3
"""Make the assistant's answers readable.

The bot returns analysis -- several paragraphs, ranked lists, often a table of doors. That
was being rendered at 14px with 4px between paragraphs, inside a bubble capped at 86% of a
420px panel. Fine for "ok, added" and unreadable for the answers that are the point of it.

Changes are all about the long answer:
  * a wider panel, and the assistant's bubble uses nearly all of it -- it is the content,
    not a chat aside. The user's own question stays narrow and right-aligned so the
    conversation still reads as a conversation.
  * 15.5px at 1.62, and real space between paragraphs and list items.
  * numbers in tabular figures so a column of dollars lines up.
  * a hairline border on the white bubble, because white-on-near-white lost its edges.

Run:  python tools/bot_legibility.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "index.html")


def main():
    s = io.open(IDX, encoding="utf-8").read()

    def sub(old, new, what):
        nonlocal s
        assert old in s, "NOT FOUND: " + what
        assert s.count(old) == 1, "AMBIGUOUS (%d): %s" % (s.count(old), what)
        s = s.replace(old, new, 1)
        print("  ok:", what)

    # a wider panel: the answers are lists and tables, not one-liners
    sub("#bot{position:absolute;bottom:18px;right:18px;z-index:1300;width:420px;",
        "#bot{position:absolute;bottom:18px;right:18px;z-index:1300;width:500px;",
        "panel 420 -> 500px")

    # the reading surface
    sub("#botmsgs{flex:1 1 auto;min-height:0;overflow-y:auto;padding:14px;background:#eef2ee;font-size:14px;line-height:1.5}",
        "#botmsgs{flex:1 1 auto;min-height:0;overflow-y:auto;padding:16px 14px;background:#eceff0;"
        "font-size:15.5px;line-height:1.62;color:#14241a;"
        "font-variant-numeric:tabular-nums}",
        "15.5px / 1.62, tabular figures")

    # the assistant's bubble is the content; the user's question is the aside
    sub(".bm .bub{display:inline-block;text-align:left;max-width:86%;padding:10px 13px;border-radius:16px;",
        ".bm .bub{display:inline-block;text-align:left;max-width:97%;padding:13px 15px;border-radius:14px;",
        "assistant bubble uses the width")
    sub(".bm.u .bub{background:linear-gradient(135deg,#239455,#1f7a44);color:#fff;border-bottom-right-radius:5px}",
        ".bm.u .bub{background:linear-gradient(135deg,#239455,#1f7a44);color:#fff;"
        "border-bottom-right-radius:5px;max-width:84%;font-size:15px}",
        "user question stays narrow")
    sub(".bm.a .bub{background:#fff;color:#14241a;border-bottom-left-radius:5px}",
        ".bm.a .bub{background:#fff;color:#14241a;border-bottom-left-radius:5px;"
        "border:1px solid #dde3dd}",
        "hairline edge on the white bubble")

    # paragraphs and lists need air; 4px ran them together
    sub(".bm .bub p,.bm .bub div{margin:0 0 4px}.bm .bub ul,.bm .bub ol{margin:6px 0;padding-left:20px}.bm .bub li{margin:2px 0}",
        ".bm .bub p,.bm .bub div{margin:0 0 11px}.bm .bub p:last-child,.bm .bub div:last-child{margin-bottom:0}\n"
        ".bm .bub ul,.bm .bub ol{margin:10px 0;padding-left:21px}.bm .bub li{margin:0 0 7px}\n"
        ".bm .bub li:last-child{margin-bottom:0}\n"
        ".bm .bub b,.bm .bub strong{font-weight:700;color:#0d1a12}\n"
        ".bm .bub h1,.bm .bub h2,.bm .bub h3,.bm .bub h4{font-size:15.5px;font-weight:700;\n"
        " margin:14px 0 7px;color:#0d1a12;letter-spacing:-.01em}\n"
        ".bm .bub h1:first-child,.bm .bub h2:first-child,.bm .bub h3:first-child{margin-top:0}\n"
        ".bm .bub hr{border:0;border-top:1px solid #e3e8e3;margin:12px 0}",
        "paragraph, list and heading rhythm")

    sub(".bm .bub code{background:#eef0ec;padding:1px 5px;border-radius:5px;font-size:13px}",
        ".bm .bub code{background:#eef0ec;padding:1px 5px;border-radius:5px;font-size:13.5px}",
        "inline code")

    # tables carry the door lists
    sub(".bm .bub table{display:block;overflow-x:auto;max-width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px}",
        ".bm .bub table{display:block;overflow-x:auto;max-width:100%;border-collapse:collapse;"
        "margin:11px 0;font-size:13.5px;line-height:1.45}",
        "table type size")
    sub(".bm .bub th,.bm .bub td{padding:5px 8px;text-align:left;border-bottom:1px solid #e7eae6}",
        ".bm .bub th,.bm .bub td{padding:7px 10px;text-align:left;border-bottom:1px solid #e7eae6}",
        "table row padding")

    # on a phone the panel is full-screen, so it can afford the same treatment
    sub("  #botmsgs{overscroll-behavior:contain;-webkit-overflow-scrolling:touch}",
        "  #botmsgs{overscroll-behavior:contain;-webkit-overflow-scrolling:touch;font-size:16px}\n"
        "  .bm .bub{max-width:99%}\n"
        "  .bm.u .bub{max-width:88%}",
        "phone: 16px and full width")

    tmp = IDX + ".new"
    io.open(tmp, "w", encoding="utf-8", newline="").write(s)
    os.replace(tmp, IDX)
    print("wrote index.html")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
