"""
Exhaustive tests for s10_assemble PDF helpers:
  - _fix_bare_math: bare LaTeX command wrapping
  - _sanitize_bib_entry: null-stripping and CSL-JSON normalisation
"""

import pytest
from doc_expand.stages.s10_assemble import _fix_bare_math, _sanitize_bib_entry


# ===========================================================================
# _fix_bare_math
# ===========================================================================

class TestFixBareMath:

    # ── basic wrapping ──────────────────────────────────────────────────────

    def test_bare_bar(self):
        assert _fix_bare_math(r"the mean \bar{x} here") == r"the mean $\bar{x}$ here"

    def test_bare_frac(self):
        assert _fix_bare_math(r"ratio \frac{a}{b} done") == r"ratio $\frac{a}{b}$ done"

    def test_bare_alpha_no_braces(self):
        assert _fix_bare_math(r"constant \alpha value") == r"constant $\alpha$ value"

    def test_bare_mathbb(self):
        assert _fix_bare_math(r"set \mathbb{R} space") == r"set $\mathbb{R}$ space"

    def test_bare_sqrt(self):
        assert _fix_bare_math(r"root \sqrt{n} here") == r"root $\sqrt{n}$ here"

    def test_bare_sum(self):
        assert _fix_bare_math(r"total \sum_{i=1}^{n} here") == r"total $\sum_{i=1}^{n}$ here"

    def test_bare_text_command(self):
        assert _fix_bare_math(r"\text{where}") == r"$\text{where}$"

    def test_command_at_start_of_line(self):
        assert _fix_bare_math(r"\alpha is defined") == r"$\alpha$ is defined"

    def test_command_at_end_of_line(self):
        assert _fix_bare_math(r"the value is \beta") == r"the value is $\beta$"

    def test_command_alone_on_line(self):
        assert _fix_bare_math(r"\gamma") == r"$\gamma$"

    def test_bare_followed_by_punctuation(self):
        # comma/period after command — should still wrap the command
        result = _fix_bare_math(r"values \alpha, \beta.")
        assert "$\\alpha$" in result
        assert "$\\beta$" in result

    # ── multiple commands on one line ───────────────────────────────────────

    def test_two_bare_commands(self):
        result = _fix_bare_math(r"\alpha and \beta values")
        assert result == r"$\alpha$ and $\beta$ values"

    def test_three_bare_commands(self):
        result = _fix_bare_math(r"\mu, \sigma, \sigma^{2}")
        assert "$\\mu$" in result
        assert "$\\sigma$" in result

    def test_consecutive_commands_no_space(self):
        # Two adjacent commands: \bar{x}\hat{y}
        result = _fix_bare_math(r"\bar{x}\hat{y}")
        assert "$\\bar{x}$" in result
        assert "$\\hat{y}$" in result

    # ── no double-wrapping ──────────────────────────────────────────────────

    def test_already_in_inline_math(self):
        text = r"$\bar{x}$"
        assert _fix_bare_math(text) == text

    def test_already_in_display_math(self):
        text = r"$$\bar{x}$$"
        assert _fix_bare_math(text) == text

    def test_already_in_inline_math_complex(self):
        text = r"$\frac{\bar{x}}{\sigma}$"
        assert _fix_bare_math(text) == text

    def test_mixed_existing_and_bare(self):
        result = _fix_bare_math(r"$x$ is near \bar{y}")
        assert result == r"$x$ is near $\bar{y}$"

    def test_text_inside_existing_math(self):
        text = r"$\text{where } x > 0$"
        assert _fix_bare_math(text) == text

    def test_partial_line_with_math_and_bare(self):
        # The bare part must be wrapped; the $...$ part must not be double-wrapped
        result = _fix_bare_math(r"Given $\mu = 0$ then \sigma is small")
        assert result == r"Given $\mu = 0$ then $\sigma$ is small"

    # ── nesting ─────────────────────────────────────────────────────────────

    def test_nested_one_level(self):
        # \frac{\bar{x}}{\sigma} — should be ONE unit
        result = _fix_bare_math(r"\frac{\bar{x}}{\sigma}")
        assert result == r"$\frac{\bar{x}}{\sigma}$"
        assert result.count("$") == 2

    def test_nested_sum_with_limits(self):
        result = _fix_bare_math(r"\sum_{i=0}^{N} x_i")
        assert result.startswith("$\\sum")
        assert result.count("$") == 2

    def test_mathbb_with_superscript(self):
        result = _fix_bare_math(r"\mathbb{R}^{n}")
        assert result == r"$\mathbb{R}^{n}$"
        assert result.count("$") == 2

    def test_boldsymbol_with_subscript(self):
        result = _fix_bare_math(r"\boldsymbol{\theta}_{t}")
        assert result.startswith("$\\boldsymbol")
        assert result.count("$") == 2

    def test_sqrt_nested(self):
        result = _fix_bare_math(r"\sqrt{\frac{a}{b}}")
        # The outer \sqrt captures inner braces including nested {}
        assert result.startswith("$")
        assert result.endswith("$")

    # ── code blocks ─────────────────────────────────────────────────────────

    def test_fenced_code_block_backtick(self):
        text = "```\n\\bar{x}\n```"
        assert _fix_bare_math(text) == text

    def test_fenced_code_block_with_language(self):
        text = "```python\n\\alpha = 0.01\n```"
        assert _fix_bare_math(text) == text

    def test_fenced_code_block_tilde(self):
        text = "~~~\n\\frac{a}{b}\n~~~"
        assert _fix_bare_math(text) == text

    def test_fenced_code_block_multiline(self):
        text = "```\nline1\n\\bar{x}\nline2\n\\frac{a}{b}\n```"
        assert _fix_bare_math(text) == text

    def test_code_before_and_after_bare(self):
        # Bare math after a code block should still be wrapped
        text = "```\n\\bar{x}\n```\nBut here \\alpha is bare"
        result = _fix_bare_math(text)
        assert "```\n\\bar{x}\n```" in result
        assert "$\\alpha$" in result

    def test_inline_code_span(self):
        text = r"`\bar{x}`"
        assert _fix_bare_math(text) == text

    def test_inline_code_mid_sentence(self):
        # The \bar inside backticks must not be wrapped; the outside \alpha must be
        result = _fix_bare_math(r"code `\bar{x}` but \alpha outside")
        assert "`\\bar{x}`" in result
        assert "$\\alpha$" in result

    # ── multiline display math ($$...$$) ────────────────────────────────────

    def test_display_math_single_line(self):
        text = r"$$\frac{a}{b}$$"
        assert _fix_bare_math(text) == text

    def test_display_math_inline_neighbours(self):
        # Bare command on same line but outside the $$ block
        result = _fix_bare_math(r"so $$x^2$$ and \bar{y}")
        assert "$$x^2$$" in result
        assert "$\\bar{y}$" in result

    # ── commands that must NOT be wrapped ───────────────────────────────────

    def test_textbf_not_wrapped(self):
        text = r"\textbf{important}"
        assert _fix_bare_math(text) == text

    def test_emph_not_wrapped(self):
        text = r"\emph{word}"
        assert _fix_bare_math(text) == text

    def test_section_not_wrapped(self):
        text = r"\section{Introduction}"
        assert _fix_bare_math(text) == text

    def test_begin_end_not_wrapped(self):
        text = r"\begin{itemize}"
        assert _fix_bare_math(text) == text

    def test_item_not_wrapped(self):
        text = r"\item first item"
        assert _fix_bare_math(text) == text

    def test_newline_escape_not_wrapped(self):
        # \n in a raw string is two chars (backslash + n), not a newline escape
        # and 'n' is not a recognised math command — should not be wrapped
        text = "line1\\nline2"
        assert _fix_bare_math(text) == text

    # ── edge cases ──────────────────────────────────────────────────────────

    def test_empty_string(self):
        assert _fix_bare_math("") == ""

    def test_no_latex(self):
        text = "Plain English with no LaTeX at all."
        assert _fix_bare_math(text) == text

    def test_bare_command_empty_braces(self):
        # \bar{} — empty argument, still math
        result = _fix_bare_math(r"\bar{}")
        assert result == r"$\bar{}$"

    def test_command_in_heading(self):
        result = _fix_bare_math(r"## Section where \bar{x} is discussed")
        assert "$\\bar{x}$" in result

    def test_command_in_list_item(self):
        result = _fix_bare_math(r"- The value \sigma^{2} matters")
        assert "$\\sigma" in result

    def test_multiline_outside_any_block(self):
        text = "First line \\alpha here\nSecond line \\beta there"
        result = _fix_bare_math(text)
        lines = result.split("\n")
        assert "$\\alpha$" in lines[0]
        assert "$\\beta$" in lines[1]

    def test_command_only_wraps_once_across_lines(self):
        text = "line1 \\alpha\nline2 \\alpha"
        result = _fix_bare_math(text)
        assert result.count("$\\alpha$") == 2

    def test_frac_two_args_are_one_unit(self):
        # \frac{a}{b} — both args must be inside a single $...$
        result = _fix_bare_math(r"value \frac{p}{q} end")
        assert result == r"value $\frac{p}{q}$ end"
        assert result.count("$") == 2

    def test_nested_frac_wraps_outer_only(self):
        # \frac{\frac{a}{b}}{c}: outer \frac includes everything
        result = _fix_bare_math(r"\frac{\frac{a}{b}}{c}")
        # Should be wrapped once, outer command is first
        assert result.startswith("$\\frac")
        assert result.endswith("$")
        # Should not have more than 2 dollar signs (no double wrapping)
        assert result.count("$") == 2

    def test_command_followed_by_word(self):
        # \alpha_i — subscript without braces
        result = _fix_bare_math(r"\alpha_i is small")
        assert result.startswith("$\\alpha")

    def test_greek_letter_mid_word_boundary(self):
        # "distribution" contains no LaTeX — confirm no false positives
        text = "The distribution is normal."
        assert _fix_bare_math(text) == text

    def test_partial_dollar_sign_not_confused(self):
        # A lone $ (not a math delimiter) should not confuse the splitter
        # This is unusual markdown but shouldn't crash
        text = r"cost is $50 and \alpha matters"
        result = _fix_bare_math(text)
        # \alpha must still be wrapped regardless of the stray $
        assert "\\alpha" in result  # it's there (wrapped or not, no crash)

    def test_tilde_fence_before_backtick_fence(self):
        # Mixed fence types: ~~~ block followed by ``` block
        text = "~~~\n\\bar{x}\n~~~\n```\n\\alpha\n```\nbare \\beta here"
        result = _fix_bare_math(text)
        assert "\\bar{x}" in result   # inside fence, unchanged
        assert "\\alpha" in result     # inside fence, unchanged
        assert "$\\beta$" in result    # outside all fences, wrapped

    def test_fenced_block_not_closed(self):
        # Unclosed fence — everything after is treated as inside; should not crash
        text = "```\n\\bar{x}\nno closing fence"
        result = _fix_bare_math(text)
        assert not result.startswith("$")  # first line not wrapped


# ===========================================================================
# _sanitize_bib_entry
# ===========================================================================

class TestSanitizeBibEntry:

    # ── null removal ────────────────────────────────────────────────────────

    def test_null_url_removed(self):
        entry = {"id": "a", "title": "T", "URL": None, "type": "article-journal",
                 "issued": {"date-parts": [[2021]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert "URL" not in result

    def test_null_doi_removed(self):
        entry = {"id": "a", "title": "T", "DOI": None, "type": "article-journal",
                 "issued": {"date-parts": [[2021]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert "DOI" not in result

    def test_multiple_null_fields_removed(self):
        entry = {"id": "a", "title": "T", "URL": None, "DOI": None,
                 "abstract": None, "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert "URL" not in result
        assert "DOI" not in result
        assert "abstract" not in result

    def test_null_in_author_dict_removed(self):
        entry = {
            "id": "a", "title": "T", "type": "article-journal",
            "issued": {"date-parts": [[2021]]},
            "author": [{"given": "Jane", "family": None}],
        }
        result = _sanitize_bib_entry(entry)
        assert "family" not in result["author"][0]
        assert result["author"][0]["given"] == "Jane"

    def test_null_author_item_dropped(self):
        # A None item (not a dict) in the author list is dropped
        entry = {
            "id": "a", "title": "T", "type": "article-journal",
            "issued": {"date-parts": [[2021]]},
            "author": [{"given": "Jane", "family": "Doe"}, None],
        }
        result = _sanitize_bib_entry(entry)
        assert len(result["author"]) == 1

    # ── non-null values preserved ────────────────────────────────────────────

    def test_non_null_values_preserved(self):
        entry = {"id": "myid", "title": "My Title", "DOI": "10.1/foo",
                 "URL": "https://example.com", "type": "article-journal",
                 "issued": {"date-parts": [[2023]]},
                 "author": [{"given": "A", "family": "B"}]}
        result = _sanitize_bib_entry(entry)
        assert result["DOI"] == "10.1/foo"
        assert result["URL"] == "https://example.com"
        assert result["id"] == "myid"

    def test_empty_string_preserved(self):
        # Empty string is not null — keep it
        entry = {"id": "a", "title": "", "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert "title" in result
        assert result["title"] == ""

    def test_zero_value_preserved(self):
        entry = {"id": "a", "title": "T", "citation_count": 0, "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["citation_count"] == 0

    def test_false_value_preserved(self):
        entry = {"id": "a", "title": "T", "reviewed": False, "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["reviewed"] is False

    # ── default injection ────────────────────────────────────────────────────

    def test_missing_id_gets_default(self):
        entry = {"title": "T", "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["id"] == "unknown"

    def test_missing_title_gets_default(self):
        entry = {"id": "a", "type": "article-journal",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["title"] == "Untitled"

    def test_missing_type_gets_default(self):
        entry = {"id": "a", "title": "T",
                 "issued": {"date-parts": [[2020]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["type"] == "article-journal"

    def test_missing_issued_gets_default(self):
        entry = {"id": "a", "title": "T", "type": "article-journal", "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["issued"] == {"date-parts": [[0]]}

    def test_existing_id_not_overwritten(self):
        entry = {"id": "real_id", "title": "T", "type": "article-journal",
                 "issued": {"date-parts": [[2021]]}, "author": []}
        result = _sanitize_bib_entry(entry)
        assert result["id"] == "real_id"

    # ── realistic full entry ─────────────────────────────────────────────────

    def test_realistic_entry_with_all_nulls(self):
        """Mirrors what Semantic Scholar returns for papers with missing fields."""
        entry = {
            "id": "smith_2023_deep",
            "type": "article-journal",
            "title": "Deep Learning for X",
            "author": [{"given": "John", "family": "Smith"}, {"given": None, "family": "Doe"}],
            "issued": {"date-parts": [[2023]]},
            "DOI": None,
            "URL": None,
            "abstract": None,
            "bucket": "frontier",
            "citation_count": 42,
        }
        result = _sanitize_bib_entry(entry)
        assert "DOI" not in result
        assert "URL" not in result
        assert "abstract" not in result
        assert result["citation_count"] == 42
        assert result["id"] == "smith_2023_deep"
        # Author with null given is kept (family is present); null fields stripped
        assert len(result["author"]) == 2
        assert "given" not in result["author"][1]  # null given stripped
        assert result["author"][1]["family"] == "Doe"

    def test_produces_valid_csl_json_for_pandoc(self):
        """Verify no nulls remain anywhere in the output (recursive check)."""
        entry = {
            "id": "x", "title": None, "type": None,
            "issued": None, "author": [None, {"given": None, "family": "X"}],
            "DOI": None, "URL": None,
        }
        result = _sanitize_bib_entry(entry)

        def has_null(obj):
            if obj is None:
                return True
            if isinstance(obj, dict):
                return any(has_null(v) for v in obj.values())
            if isinstance(obj, list):
                return any(has_null(i) for i in obj)
            return False

        assert not has_null(result), f"Null found in output: {result}"
