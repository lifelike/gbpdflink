#!/usr/bin/env python

"""gbpdflink.py

Script to (attempt to) add links to gamebook PDFs.

Expecting two arguments:
input PDF file
output PDF file

TODO: Add some options to configure script to handle
more books. It is currently very simplistic and
makes too many assumptions about gamebook layout.

Copyright 2013 Pelle Nilsson <perni@lysator.liu.se>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE."""

import os.path
import sys
from operator import attrgetter
from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams, LTChar, LTTextLineHorizontal, LTContainer
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pyPdf import PdfFileReader, PdfFileWriter
from reportlab.pdfgen import canvas
from StringIO import StringIO

# This is to avoid having to sum page heights.
# Only interested in having an order of positions
# so that higher page numbers are considered
# below anything on lower page.
LARGER_THAN_ANY_REAL_PAGE_HEIGHT = 100000

class Position(object):
    def __init__(self, pagenr, x0, x1, y0, y1, char_start, char_end):
        self.pagenr = pagenr
        self.x0 = x0
        self.x1 = x1
        self.y0 = y0
        self.y1 = y1
        self.char_start = char_start
        self.char_end = char_end
        self.doc_y = pagenr * LARGER_THAN_ANY_REAL_PAGE_HEIGHT - y0

    def __cmp__(self, other):
        return self.doc_y - other.doc_y

class Number(object):
    def __init__(self, value, pos, line):
        self.value = value
        self.pos = pos
        self.line = line

    def __str__(self):
        return ', '.join([str(x) for x in [self.value,
                                           self.pos.pagenr,
                                           self.pos.x0,
                                           self.pos.x1,
                                           self.pos.y0,
                                           self.pos.y1,
                                           len(self.line)]])

def add_section_links_to_pdf(args):
    infilename = args.inputfilename
    outfilename = args.outputfilename
    print "Analyzing", infilename, "looking for targets and links..."
    numbers = sorted(find_numbers(infilename), key=attrgetter('pos'))
    print "Found", len(numbers), "in input PDF."
    print "Trying to make a guess what numbers are links or link targets..."
    [links, targets] = guess_what_numbers_are(numbers, args.startpage-1,
                                              args.sectionformat)
    print "Found", len(targets), "targets, and", len(links), "links."
    if len(targets) == 0 or len(links) == 0:
        print "Nothing to add, so quitting."
        sys.exit(0)
    print "Lowest target:", min([t.value for t in targets])
    print "Highest target:", max([t.value for t in targets])
    valid_links = keep_links_to_existing_targets_only(links, targets)
    print len(valid_links), "links goes to existing targets."
    print "Getting page size from input PDF..."
    pagesize = get_page_size(infilename)
    print "PDF page size:", pagesize
    print "Getting number of pages in input PDF..."
    nrpages = get_nr_pages(infilename)
    print "PDF pages:", nrpages
    write_pdf_with_links(infilename, valid_links, targets, pagesize,
                         nrpages, outfilename)

def find_numbers(filename):
    f = open(filename)
    parser = PDFParser(f)
    doc = PDFDocument(parser)
    laparams = LAParams()
    resource_manager = PDFResourceManager()
    device = PDFPageAggregator(resource_manager, laparams=laparams)
    interpreter = PDFPageInterpreter(resource_manager, device)
    numbers = []
    for pagenr,page in enumerate(PDFPage.create_pages(doc)):
        interpreter.process_page(page)
        numbers.extend(find_numbers_in_layout(pagenr, device.get_result()))
    return numbers

def find_numbers_in_layout(pagenr, part):
    numbers = []
    if isinstance(part, LTTextLineHorizontal):
        numbers.extend(find_numbers_in_line(pagenr, part))
    elif isinstance(part, LTContainer):
        for child in part:
            numbers.extend(find_numbers_in_layout(pagenr, child))
    return numbers

def find_numbers_in_line(pagenr, line):
    numbers = []
    line_text = line.get_text()
    line_chars = len(line_text)
    current_number = None
    for charnr,char in enumerate(line):
        try:
            n = int(char.get_text())
            if current_number:
                current_number = current_number + char.get_text()
                current_number_x1 = char.x1
                current_number_y0 = min(current_number_y0, char.y0)
                current_number_y1 = max(current_number_y1, char.y1)
            else:
                current_number = char.get_text()
                current_number_start = charnr
                current_number_x0 = char.x0
                current_number_x1 = char.x1
                current_number_y0 = char.y0
                current_number_y1 = char.y1
        except:
            if current_number:
                numbers.append(Number(int(current_number),
                                      Position(pagenr,
                                               current_number_x0,
                                               current_number_x1,
                                               current_number_y0,
                                               current_number_y1,
                                               current_number_start,
                                               charnr), line_text))
                current_number = None
    if current_number:
        numbers.append(Number(int(current_number),
                              Position(pagenr,
                                       current_number_x0,
                                       current_number_x1,
                                       current_number_y0,
                                       current_number_y1,
                                       current_number_start,
                                       line_chars), line_text))
    return numbers

# Obviously this only works for books formatted with
# section numbers on a line of their own above the section.
# Luckily that is very common, so ignoring the more difficult
# task of detecting numbers in other formatting styles for now.
# Having some margins here because some books have other symbols
# than just the number in the section heading.
MAX_CHARS_TO_CONSIDER_HEADING = 6

def guess_what_numbers_are(numbers, startpage, sectionformat):
    """Just using a very simple heuristics that a number that is on its own
    on a line is probably a section heading (link target), while any other
    number is a link.

    The last_target_value_was_expected code is here because if a section ends
    in a number (link) on a line of its own, that is going to be detected as
    a target (section heading) and confuse things. So if a target is not
    expected (but still higher than the expected) then the next target found
    if that has the previously expected value will be detected (and of course
    the last added target is assumed to be a link instead).

    Ignore targets before given startpage.

    Returns list of links (list of Numbers)
    followed by targets (list of Numbers).
    """
    links = []
    targets = []
    next_expected_target_value = 1
    last_expected_target_value = 0

    def has_section_number_format(number):
        expected_line = (sectionformat % number.value)
        return (expected_line == number.line
                or (len(number.line) > 1
                    and expected_line == (number.line[:-1])))

    def last_target_was_higher_than_expected():
        return len(targets) and targets[-1].value > last_expected_target_value

    def is_likely_target(value):
        return ((next_expected_target_value == 1
                 and value == 1)
                or (next_expected_target_value > 1
                 and value >= next_expected_target_value)
                or last_target_was_higher_than_expected())

    for number in numbers:
        suspect_target = False
        if (has_section_number_format(number)
            and number.pos.pagenr >= startpage
            and is_likely_target(number.value)):
            if last_target_was_higher_than_expected():
                links.append(targets.pop())
            targets.append(number)
            last_expected_target_value = next_expected_target_value
            next_expected_target_value = number.value + 1
        else:
            links.append(number)
    return [links, targets]

def keep_links_to_existing_targets_only(links, targets):
    targets_value_set = set([t.value for t in targets])
    return filter(lambda link: link.value in targets_value_set, links)

def get_page_size(infilename):
    # sloppily not closing file here, but that is not important
    mediaBox = PdfFileReader(open(infilename, "rb")).getPage(0).mediaBox
    return (mediaBox.getWidth(), mediaBox.getHeight())

def get_nr_pages(infilename):
    # sloppily not closing file here, but that is not important
    return PdfFileReader(open(infilename, "rb")).getNumPages()


def write_pdf_with_links(infilename, links, targets, pagesize, nrpages, outfilename):
    print "Writing", outfilename, "with links added to input PDF."
    print "(Actually just debugging now, so writing some text.)"
    links_targets_pdf = create_pdf(targets, links, pagesize, nrpages)
    links_targets_file = StringIO(links_targets_pdf)
    merge_input_with_links_to_output(infilename, links_targets_file,
                                     outfilename)

def create_pdf(targets, links, pagesize, nrpages):
    print "Creating PDF with links and targets..."
    output = StringIO()
    c = canvas.Canvas(output, pagesize=pagesize)
    pagenr = min(targets[0].pos.pagenr, links[0].pos.pagenr)
    for p in range(pagenr):
        c.showPage() #empty pages before first number
    for p in range(pagenr, nrpages):
        while (len(targets)
               and targets[0].pos.pagenr == p):
            target = targets.pop(0)
            print "Adding target", target.value, "to page", (p+1)
            c.setStrokeColorRGB(1, 0, 0)
            x = target.pos.x0
            y = target.pos.y0
            width = target.pos.x1 - target.pos.x0
            height = target.pos.y1 - target.pos.y0
            c.rect(x, y, width, height)
            c.bookmarkPage("number" + str(target.value), fit="XYZ",
                           top=target.pos.y1)
        while (len(links)
               and links[0].pos.pagenr == p):
            link = links.pop(0)
            print "Adding link", link.value, "to page", (p+1)
            c.setStrokeColorRGB(0, 1, 0)
            x = link.pos.x0
            y = link.pos.y0
            width = link.pos.x1 - link.pos.x0
            height = link.pos.y1 - link.pos.y0
            c.rect(x, y, width, height)
            rect = (link.pos.x0, link.pos.y0,
                    link.pos.x1, link.pos.y1)
            c.linkRect(str(link.value),
                       "number" + str(link.value),
                        rect)
        c.showPage()
    c.save()
    pdf = output.getvalue()
    output.close()
    return pdf

def merge_input_with_links_to_output(infilename, links_targets_file,
                                     outfilename):
    """Merge PDF containing only links and targets to the original
    input PDF. Note that PdPDF does not support merging annotations,
    so the merge has to be from the original file to the file containing
    the new annotations (and any original annotations will be lost)."""
    print "Adding links..."
    input = PdfFileReader(open(infilename, "rb"))
    output = PdfFileWriter()
    links_targets_input = PdfFileReader(links_targets_file)
    for p in range(input.getNumPages()):
        print "Merging page", (p+1), "..."
        page = links_targets_input.getPage(p)
        page.mergePage(input.getPage(p))
        output.addPage(page)
    output.write(open(outfilename, "wb"))

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(formatter_class
                                 =argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputfilename',
                    help="input gamebook PDF.")
    ap.add_argument('outputfilename',
                    help="output gamebook PDF with links added")
    ap.add_argument('-p', '--start-page', metavar='N',
                    dest='startpage',
                    default=0, type=int,
                    help='first page to look for sections')
    ap.add_argument('-f', '--section-format', metavar='F',
                    dest='sectionformat',
                    default='%d',
                    help='section number accepted format')
    add_section_links_to_pdf(ap.parse_args())
