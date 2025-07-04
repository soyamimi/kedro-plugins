"""``LazyPolarsDataset`` loads/saves data from/to a data file using an underlying
filesystem (e.g.: local, S3, GCS). It uses polars to handle the
type of read/write target.
"""
from __future__ import annotations

import errno
import logging
import os
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, ClassVar

import fsspec
import polars as pl
import pyarrow.dataset as ds
from kedro.io.core import (
    AbstractVersionedDataset,
    DatasetError,
    Version,
    get_filepath_str,
    get_protocol_and_path,
)

ACCEPTED_FILE_FORMATS = ["csv", "parquet"]

PolarsFrame = pl.LazyFrame | pl.DataFrame

logger = logging.getLogger(__name__)


class LazyPolarsDataset(
    AbstractVersionedDataset[pl.LazyFrame, pl.LazyFrame | pl.DataFrame]
):
    """``LazyPolarsDataset`` loads/saves data from/to a data file using an
    underlying filesystem (e.g.: local, S3, GCS). It uses Polars to handle
    the type of read/write target. It uses lazy loading with Polars Lazy API, but it can
    save both Lazy and Eager Polars DataFrames.

    Examples:
        Using the [YAML API](https://docs.kedro.org/en/stable/data/data_catalog_yaml_examples.html):

        ```yaml
        cars:
          type: polars.LazyPolarsDataset
          filepath: data/01_raw/company/cars.csv
          load_args:
            sep: ","
            parse_dates: False
          save_args:
            has_header: False
            null_value: "somenullstring"

        motorbikes:
          type: polars.LazyPolarsDataset
          filepath: s3://your_bucket/data/02_intermediate/company/motorbikes.csv
          credentials: dev_s3
        ```

        Using the [Python API](https://docs.kedro.org/en/stable/data/advanced_data_catalog_usage.html):

        >>> import polars as pl
        >>> from kedro_datasets.polars import LazyPolarsDataset
        >>>
        >>> data = pl.DataFrame({"col1": [1, 2], "col2": [4, 5], "col3": [5, 6]})
        >>>
        >>> dataset = LazyPolarsDataset(filepath=tmp_path / "test.csv", file_format="csv")
        >>> dataset.save(data)
        >>> reloaded = dataset.load()
        >>> assert data.equals(reloaded.collect())

    """

    DEFAULT_LOAD_ARGS: ClassVar[dict[str, Any]] = {}
    DEFAULT_SAVE_ARGS: ClassVar[dict[str, Any]] = {}
    DEFAULT_FS_ARGS: dict[str, Any] = {"open_args_save": {"mode": "wb"}}

    def __init__(  # noqa: PLR0913
        self,
        *,
        filepath: str,
        file_format: str,
        load_args: dict[str, Any] | None = None,
        save_args: dict[str, Any] | None = None,
        version: Version | None = None,
        credentials: dict[str, Any] | None = None,
        fs_args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Creates a new instance of ``LazyPolarsDataset`` pointing to a concrete
        data file on a specific filesystem.

        Args:
            filepath: Filepath in POSIX format to a file prefixed with a protocol like
                `s3://`.
                If prefix is not provided, `file` protocol (local filesystem)
                will be used.
                The prefix should be any protocol supported by ``fsspec``.
                Key assumption: The first argument of either load/save method points to
                a filepath/buffer/io type location. There are some read/write targets
                such as 'clipboard' or 'records' that will fail since they do not take a
                filepath like argument.
            file_format: String which is used to match the appropriate load/save method
                on a best effort basis. For example if 'csv' is passed the
                `polars.read_csv` and
                `polars.DataFrame.write_csv` methods will be identified. An error will
                be raised unless
                at least one matching `read_{file_format}` or `write_{file_format}`.
            load_args: polars options for loading files.
                Here you can find all available arguments:
                https://pola-rs.github.io/polars/py-polars/html/reference/io.html
                All defaults are preserved.
            save_args: Polars options for saving files.
                Here you can find all available arguments:
                https://pola-rs.github.io/polars/py-polars/html/reference/io.html
                All defaults are preserved.
            version: If specified, should be an instance of
                ``kedro.io.core.Version``. If its ``load`` attribute is
                None, the latest version will be loaded. If its ``save``
                attribute is None, save version will be autogenerated.
            credentials: Credentials required to get access to the underlying filesystem.
                E.g. for ``GCSFileSystem`` it should look like `{"token": None}`.
            fs_args: Extra arguments to pass into underlying filesystem class constructor
                (e.g. `{"project": "my-project"}` for ``GCSFileSystem``), as well as
                to pass to the filesystem's `open` method through nested keys
                `open_args_load` and `open_args_save`.
                Here you can find all available arguments for `open`:
                https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem.open
                All defaults are preserved, except `mode`, which is set to `wb` when saving.
            metadata: Any arbitrary metadata.
                This is ignored by Kedro, but may be consumed by users or external plugins.
        Raises:
            DatasetError: Will be raised if at least less than one appropriate
                read or write methods are identified.
        """
        self._file_format = file_format.lower()

        if self._file_format not in ACCEPTED_FILE_FORMATS:
            raise DatasetError(
                f"'{self._file_format}' is not an accepted format "
                f"({ACCEPTED_FILE_FORMATS}) ensure that your 'file_format' parameter "
                "has been defined correctly as per the Polars API "
                "https://pola-rs.github.io/polars/py-polars/html/reference/io.html"
            )

        _fs_args = deepcopy(fs_args) or {}
        _fs_open_args_load = _fs_args.pop("open_args_load", {})
        _fs_open_args_save = _fs_args.pop("open_args_save", {})
        _credentials = deepcopy(credentials) or {}

        protocol, path = get_protocol_and_path(filepath, version)
        if protocol == "file":
            _fs_args.setdefault("auto_mkdir", True)

        self._protocol = protocol
        self._storage_options = {**_credentials, **_fs_args}
        self._fs = fsspec.filesystem(self._protocol, **self._storage_options)

        self.metadata = metadata

        super().__init__(
            filepath=PurePosixPath(path),
            version=version,
            exists_function=self._fs.exists,
            glob_function=self._fs.glob,
        )

        # Handle default load and save and fs arguments
        self._load_args = {**self.DEFAULT_LOAD_ARGS, **(load_args or {})}
        self._save_args = {**self.DEFAULT_SAVE_ARGS, **(save_args or {})}
        self._fs_open_args_load = {
            **self.DEFAULT_FS_ARGS.get("open_args_load", {}),
            **(_fs_open_args_load or {}),
        }
        self._fs_open_args_save = {
            **self.DEFAULT_FS_ARGS.get("open_args_save", {}),
            **(_fs_open_args_save or {}),
        }

        if "storage_options" in self._save_args or "storage_options" in self._load_args:
            logger.warning(
                "Dropping 'storage_options' for %s, "
                "please specify them under 'fs_args' or 'credentials'.",
                self._filepath,
            )
            self._save_args.pop("storage_options", None)
            self._load_args.pop("storage_options", None)

    def _describe(self) -> dict[str, Any]:
        return {
            "filepath": self._filepath,
            "protocol": self._protocol,
            "load_args": self._load_args,
            "save_args": self._save_args,
            "version": self._version,
        }

    def load(self) -> pl.LazyFrame:
        load_path = str(self._get_load_path())
        if not self._exists():
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), load_path)

        if self._protocol == "file":
            # With local filesystems, we can use Polar's build-in I/O method:
            load_method = getattr(pl, f"scan_{self._file_format}", None)
            return load_method(load_path, **self._load_args)  # type: ignore[misc]

        # For object storage, we use pyarrow for I/O:
        dataset = ds.dataset(
            load_path, filesystem=self._fs, format=self._file_format, **self._load_args
        )
        return pl.scan_pyarrow_dataset(dataset)

    def save(self, data: pl.DataFrame | pl.LazyFrame) -> None:
        save_path = get_filepath_str(self._get_save_path(), self._protocol)

        collected_data = None
        if isinstance(data, pl.LazyFrame):
            collected_data = data.collect()
        else:
            collected_data = data

        # Note: polars does support writing partitioned parquet file
        # it is leveraging Arrow to do so, see e.g.
        # https://pola-rs.github.io/polars/py-polars/html/reference/api/polars.DataFrame.write_parquet.html
        save_method = getattr(collected_data, f"write_{self._file_format}", None)
        if save_method:
            with self._fs.open(save_path, **self._fs_open_args_save) as fs_file:
                save_method(file=fs_file, **self._save_args)

                self._invalidate_cache()
        # How the LazyPolarsDataset logic is currently written with
        # ACCEPTED_FILE_FORMATS and a check in the `__init__` method,
        # this else loop is never reached, hence we exclude it from coverage report
        # but leave it in for consistency between the Eager and Lazy classes
        else:  # pragma: no cover
            raise DatasetError(
                f"Unable to retrieve 'polars.DataFrame.write_{self._file_format}' "
                "method, please ensure that your 'file_format' parameter has been "
                "defined correctly as per the Polars API"
                "https://pola-rs.github.io/polars/py-polars/html/reference/dataframe/index.html"
            )

    def _exists(self) -> bool:
        try:
            load_path = get_filepath_str(self._get_load_path(), self._protocol)
        except DatasetError:  # pragma: no cover
            return False

        return self._fs.exists(load_path)

    def _release(self) -> None:
        super()._release()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Invalidate underlying filesystem caches."""
        filepath = get_filepath_str(self._filepath, self._protocol)
        self._fs.invalidate_cache(filepath)
