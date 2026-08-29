"""The TypeScript row interface must match the pydantic one.

The two were maintained by hand and drifted: `also_listed_as` was added to the
Python model and shipped in stocks.json while types.ts never learned about it,
so the frontend was typed against a dataset that no longer existed. types.ts is
now generated, and this test is what keeps it honest -- without it, the
generator is merely available rather than binding.
"""
from pipeline import emit_types
from pipeline.schema import Stock


class TestGeneratedTypes:
    def test_types_ts_is_up_to_date(self):
        """Run `python -m pipeline.emit_types` if this fails."""
        assert emit_types.main(["--check"]) == 0

    def test_every_schema_field_appears(self):
        rendered = emit_types.render()
        for name in Stock.model_fields:
            assert f"  {name}: " in rendered

    def test_optional_floats_are_nullable_numbers(self):
        assert "  trailing_pe: number | null" in emit_types.render()

    def test_required_fields_are_not_nullable(self):
        rendered = emit_types.render()
        assert "  symbol: string\n" in rendered
        assert "  rank: number\n" in rendered

    def test_list_fields_render_as_arrays(self):
        assert "  also_listed_as: string[]" in emit_types.render()

    def test_literals_keep_their_union(self):
        assert "  fundamentals_source: 'sec' | 'yahoo' | 'none'" in emit_types.render()
