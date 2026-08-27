from __future__ import annotations

import pyarrow as pa

from org_harvest.parquet_schema import dataset_schema, field_type


class TestFieldType:
    def test_known_bool_field(self):
        assert field_type("is_private") == pa.bool_()

    def test_known_int_field(self):
        assert field_type("database_id") == pa.int64()

    def test_unknown_field_defaults_to_string(self):
        assert field_type("created_at") == pa.string()
        assert field_type("anything_else") == pa.string()


class TestDatasetSchema:
    def test_schema_field_order_matches_input(self):
        schema = dataset_schema(("id", "database_id", "is_private"))
        assert schema.names == ["id", "database_id", "is_private"]

    def test_every_field_is_nullable(self):
        schema = dataset_schema(("id", "database_id"))
        for field in schema:
            assert field.nullable

    def test_schema_is_deterministic_for_the_same_field_list(self):
        assert dataset_schema(("id", "name")) == dataset_schema(("id", "name"))
