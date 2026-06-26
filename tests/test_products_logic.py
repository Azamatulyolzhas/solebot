"""Unit tests for pure catalog/CSV helpers in products.py.

The CSV parser is a real customer-data entry point (Excel exports, 1C, etc.)
so the numeric-coercion edge cases matter.
"""
import pytest

import products


class TestParseAttributes:
    def test_json_string(self):
        assert products._parse_attributes('{"size": 42}') == {"size": 42}

    def test_dict_passthrough(self):
        assert products._parse_attributes({"a": 1}) == {"a": 1}

    def test_invalid_json_returns_empty(self):
        assert products._parse_attributes("not json") == {}

    def test_none_and_empty(self):
        assert products._parse_attributes(None) == {}
        assert products._parse_attributes("") == {}


class TestParseAttributesCell:
    def test_semicolon_kv_pairs(self):
        assert products._parse_attributes_cell("size:42;color:белый") == {
            "size": 42,
            "color": "белый",
        }

    def test_json_object_cell(self):
        assert products._parse_attributes_cell('{"size": 42}') == {"size": 42}

    def test_blank(self):
        assert products._parse_attributes_cell("") == {}
        assert products._parse_attributes_cell(None) == {}


class TestParseIntCell:
    def test_plain_int(self):
        assert products._parse_int_cell("45000", "price") == 45000

    def test_excel_float_string(self):
        # Excel often exports integers as "13990.0"
        assert products._parse_int_cell("13990.0", "price") == 13990

    def test_thousands_with_spaces(self):
        assert products._parse_int_cell("1 000", "price") == 1000

    def test_blank_is_zero(self):
        assert products._parse_int_cell("", "quantity") == 0
        assert products._parse_int_cell(None, "quantity") == 0

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            products._parse_int_cell("abc", "price")


class TestParseProductCsv:
    def test_valid_csv(self):
        csv_bytes = (
            "name,price,quantity,sku,category\n"
            "Nike Air Force 1,45000,5,AF1,Кроссовки\n"
        ).encode("utf-8")
        result = products.parse_product_csv(csv_bytes)
        assert len(result) == 1
        assert result[0]["name"] == "Nike Air Force 1"
        assert result[0]["price"] == 45000
        assert result[0]["quantity"] == 5

    def test_missing_required_column_raises(self):
        csv_bytes = b"name,sku\nNike,AF1\n"
        with pytest.raises(ValueError, match="Missing columns"):
            products.parse_product_csv(csv_bytes)

    def test_invalid_row_reports_line(self):
        csv_bytes = (
            "name,price,quantity\n"
            "Bad Product,0,5\n"  # price must be > 0
        ).encode("utf-8")
        with pytest.raises(ValueError, match="row 2"):
            products.parse_product_csv(csv_bytes)

    def test_negative_quantity_rejected(self):
        csv_bytes = b"name,price,quantity\nNike,45000,-1\n"
        with pytest.raises(ValueError):
            products.parse_product_csv(csv_bytes)


class TestIsBrowseQuery:
    def test_browse_terms(self):
        assert products.is_browse_query("покажи каталог")
        assert products.is_browse_query("что есть")
        assert products.is_browse_query("весь ассортимент")

    def test_specific_query_is_not_browse(self):
        assert not products.is_browse_query("красные найки 42 размер")


# Mirrors the real catalog: colour stored in attributes as a Russian word, name
# in English ('Puma Suede Classic Blue' with attributes color:синий).
_BLUE = {"id": 1, "name": "Puma Suede Classic Blue",
         "description": "синие с белой полосой", "attributes": {"size": 42, "color": "синий"}}
_BLACK = {"id": 2, "name": "Vans Old Skool Black",
          "description": "низкие кеды", "attributes": {"size": 42, "color": "чёрный"}}
_BLACK43 = {"id": 3, "name": "Vans Old Skool Black",
            "description": "низкие кеды", "attributes": {"size": 43, "color": "чёрный"}}
_NOCOLOR = {"id": 4, "name": "Куртка", "description": "тёплая", "attributes": {"size": "M"}}


class TestExtractAttributeFilters:
    def test_color_inflections_yield_root(self):
        assert products.extract_attribute_filters("синий")["color"] == "син"
        assert products.extract_attribute_filters("синие кеды")["color"] == "син"

    def test_yo_folding(self):
        # 'чёрный' (ё) and 'черный' (е) both resolve to the same root.
        assert products.extract_attribute_filters("чёрный")["color"] == "черн"
        assert products.extract_attribute_filters("черные")["color"] == "черн"

    def test_noun_starting_like_a_colour_is_not_a_colour(self):
        # 'синтетика' must NOT be read as the colour 'синий'.
        assert "color" not in products.extract_attribute_filters("синтетика")

    def test_size(self):
        assert products.extract_attribute_filters("размер 43")["size"] == "43"
        assert products.extract_attribute_filters("нужен 42")["size"] == "42"

    def test_model_number_is_not_a_size(self):
        assert "size" not in products.extract_attribute_filters("New Balance 574")
        assert "size" not in products.extract_attribute_filters("Air Max 90")


class TestApplyAttrFilters:
    def test_blue_query_drops_black(self):
        out = products._apply_attr_filters("синий", [_BLACK, _BLUE])
        assert out == [_BLUE]

    def test_black_query_matches_stored_yo(self):
        # query 'черный' (no ё) must still match stored 'чёрный' (ё).
        out = products._apply_attr_filters("черный", [_BLUE, _BLACK])
        assert out == [_BLACK]

    def test_blue_with_no_blue_returns_empty(self):
        # Honest empty — never fall back to the wrong colour.
        assert products._apply_attr_filters("синий", [_BLACK]) == []

    def test_color_skipped_when_catalog_has_no_colour(self):
        # Shop that doesn't track colour must not be emptied out.
        assert products._apply_attr_filters("синий", [_NOCOLOR]) == [_NOCOLOR]

    def test_size_narrows_when_present(self):
        out = products._apply_attr_filters("43", [_BLACK, _BLACK43])
        assert out == [_BLACK43]

    def test_size_ignored_when_no_match(self):
        # A size nobody has must not wipe the results.
        out = products._apply_attr_filters("49", [_BLACK, _BLACK43])
        assert out == [_BLACK, _BLACK43]
