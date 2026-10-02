"""Matrix elements by row and column (parserx.eval.matrices, from the unified evaluator)."""

from parserx.eval.matrices import compute_matrix_metrics, matrices

# a sparse matrix whose last row's b_n belongs in the fourth column
EXPECTED = (r"$$A=\begin{pmatrix} b_1 & & & \boldsymbol{0} \\ b_2 & c_2 & & \\ & \ddots & \ddots & \\ "
            r"\boldsymbol{0} & & & b_n \end{pmatrix}$$")
SHIFTED = EXPECTED.replace(r"\boldsymbol{0} & & & b_n", r"\boldsymbol{0} & & b_n &")


def test_a_matrix_is_read_as_rows_and_columns():
    (grid,) = matrices(EXPECTED)
    assert grid[0] == ["b_1", "", "", "0"] and grid[3] == ["0", "", "", "b_n"]


def test_an_element_in_the_wrong_column_is_counted():
    same = compute_matrix_metrics(EXPECTED, EXPECTED)
    shifted = compute_matrix_metrics(SHIFTED, EXPECTED)
    assert (same.elements, same.in_place, same.extra) == (8, 8, 0)
    assert (shifted.in_place, shifted.extra) == (7, 1)


def test_notation_differences_are_not_errors():
    other = (r"$$A=\left(\begin{matrix}{b_{1}}&&&{\mathbf{0}}\\{b_{2}}&{c_{2}}&&\\&{\ddots}&{\ddots}&\\"
             r"{\mathbf{0}}&&&{b_{n}}\end{matrix}\right)$$")
    assert compute_matrix_metrics(other, EXPECTED).in_place == 8


def test_an_array_and_a_nested_matrix():
    cases = r"$$\mu:=\left\{\begin{array}{ll}0.1\mu, & \eta>0.75;\\ 10\mu, & \eta<0.25.\end{array}\right.$$"
    assert matrices(cases) == [[["0.1μ,", "η>0.75;"], ["10μ,", "η<0.25."]]]
    nested = r"$$\begin{bmatrix}\delta_a\\ \delta_b\end{bmatrix}=[F]\begin{bmatrix}[K]^{-1}_a\\ [K]^{-1}_b\end{bmatrix}$$"
    assert len(matrices(nested)) == 2


def test_a_matrix_not_written_is_missing():
    metrics = compute_matrix_metrics("![公式](images/a.png)", EXPECTED)
    assert (metrics.elements, metrics.in_place, metrics.paired) == (8, 0, 0)


def test_a_column_added_to_a_correct_matrix_is_extra_and_another_size():
    expected = r"$$\begin{pmatrix} a & b \\ c & d \end{pmatrix}$$"
    zeros = r"$$\begin{pmatrix} 0 & a & b \\ 0 & c & d \end{pmatrix}$$"
    row = r"$$\begin{pmatrix} a & b \\ c & d \\ e & f \end{pmatrix}$$"
    for output in (zeros, row):
        metrics = compute_matrix_metrics(output, expected)
        assert (metrics.in_place, metrics.extra, metrics.other_size) == (4, 2, 1)


def test_cases_and_array_are_one_notation():
    array = r"$$f=\left\{\begin{array}{ll} 1, & x>0; \\ 0, & x\le 0. \end{array}\right.$$"
    cases = r"$$f=\begin{cases} 1, & x>0; \\ 0, & x\le 0. \end{cases}$$"
    metrics = compute_matrix_metrics(cases, array)
    assert (metrics.in_place, metrics.extra) == (4, 0)
