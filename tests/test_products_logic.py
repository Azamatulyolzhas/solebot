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
