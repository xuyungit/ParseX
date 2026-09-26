"""Grouping native text lines into paragraphs (Q80): detector regions first, geometry only where no region is."""

from dataclasses import dataclass

from parserx.content.paragraphs import group_lines


@dataclass
class Line:
    bbox: tuple[float, float, float, float]
    size: float = 10.0
    direction: tuple[float, float] = (1.0, 0.0)
    block: int = 0


def _row(y, x0=72, x1=520, size=10.0, block=0):
    return Line((x0, y, x1, y + size * 1.2), size, block=block)


def _text(y0, y1):
    return ("text", (60, y0, 540, y1))


def test_pieces_of_one_line_are_joined_again_inside_a_region():
    # a PDF library may cut a line at every change of font (MuPDF 1.28 in formula-heavy text)
    lines = [Line((72, 100, 110, 112)), Line((113, 100, 160, 112), block=1), Line((163, 100, 300, 112), block=2),
             _row(114, block=3)]
    assert group_lines(lines, [_text(95, 130)]) == [[0, 1, 2, 3]]


def test_regions_separate_paragraphs_the_spacing_cannot():
    lines = [_row(100), _row(114), _row(128), _row(142)]  # evenly spaced: no gap between the two paragraphs
    assert group_lines(lines, [_text(95, 126), _text(127, 160)]) == [[0, 1], [2, 3]]


def test_a_paragraph_number_far_left_on_the_same_row_stays_with_its_text():
    lines = [Line((72, 100, 110, 112)), Line((150, 100, 520, 112)), Line((150, 114, 520, 126))]  # "[0005]" | text
    assert group_lines(lines, [_text(95, 130)]) == [[0, 1, 2]]


def test_a_change_of_size_inside_a_region_starts_a_new_paragraph():
    lines = [_row(100, size=14), _row(120), _row(134)]
    assert group_lines(lines, [_text(95, 150)]) == [[0], [1, 2]]


def test_lines_outside_every_region_are_grouped_by_their_spacing():
    # code lines the detector did not frame: evenly spaced lines form one group, a wider gap ends it
    lines = [_row(300), _row(312), _row(324), _row(360), _row(372)]
    assert group_lines(lines, [_text(95, 130)]) == [[0, 1, 2], [3, 4]]
    assert group_lines(lines, None) == [[0, 1, 2], [3, 4]]  # no detector: the same geometry for every line


def test_lines_in_table_or_figure_regions_are_not_one_paragraph():
    labels = [Line((72, 100, 100, 112)), Line((300, 100, 330, 112)), Line((72, 300, 100, 312))]  # chart labels
    body = [_row(y) for y in (400, 414, 428, 442)]  # the page's text: its own line spacing
    groups = group_lines(labels + body, [("chart", (60, 90, 540, 320)), _text(395, 460)])
    assert sorted(map(sorted, groups)) == [[0], [1], [2], [3, 4, 5, 6]]


def test_rotated_lines_keep_the_grouping_they_came_with():
    # a diagonal watermark is not prose: its lines stay as the library grouped them
    lines = [_row(100), Line((100, 400, 300, 500), direction=(0.7, -0.7), block=5),
             Line((120, 420, 320, 520), direction=(0.7, -0.7), block=5)]
    assert sorted(map(sorted, group_lines(lines, [_text(95, 130)]))) == [[0], [1, 2]]
