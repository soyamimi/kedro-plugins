"""``ParquetDataset`` loads/saves data from/to a Parquet file using an underlying
filesystem (e.g.: local, S3, GCS). It uses pandas to handle the Parquet file.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

import fsspec
import pandas as pd
from kedro.io.core import (
    PROTOCOL_DELIMITER,
    AbstractVersionedDataset,
    DatasetError,
    Version,
    get_filepath_str,
    get_protocol_and_path,
)

from kedro_datasets._typing import TablePreview

logger = logging.getLogger(__name__)


class ParquetDataset(AbstractVersionedDataset[pd.DataFrame, pd.DataFrame]):
    """``ParquetDataset`` loads/saves data from/to a Parquet file using an underlying
    filesystem (e.g.: local, S3, GCS). It uses pandas to handle the Parquet file.

    Examples:
        Using the [YAML API](https://docs.kedro.org/en/stable/data/data_catalog_yaml_examples.html):

        ```yaml
        boats:
          type: pandas.ParquetDataset
          filepath: data/01_raw/boats.parquet
          load_args:
            engine: pyarrow
            use_nullable_dtypes: True
          save_args:
            file_scheme: hive
            has_nulls: False
            engine: pyarrow

        trucks:
          type: pandas.ParquetDataset
          filepath: abfs://container/02_intermediate/trucks.parquet
          credentials: dev_abs
          load_args:
            columns: [name, gear, disp, wt]
            index: name
          save_args:
            compression: GZIP
            partition_on: [name]
        ```

        Using the [Python API](https://docs.kedro.org/en/stable/data/advanced_data_catalog_usage.html):

        >>> import pandas as pd
        >>> from kedro_datasets.pandas import ParquetDataset
        >>>
        >>> data = pd.DataFrame({"col1": [1, 2], "col2": [4, 5], "col3": [5, 6]})
        >>>
        >>> dataset = ParquetDataset(filepath=tmp_path / "test.parquet")
        >>> dataset.save(data)
        >>> reloaded = dataset.load()
        >>> assert data.equals(reloaded)
    """

    DEFAULT_LOAD_ARGS: dict[str, Any] = {}
    DEFAULT_SAVE_ARGS: dict[str, Any] = {}
    DEFAULT_FS_ARGS: dict[str, Any] = {"open_args_save": {"mode": "wb"}}

    def __init__(  # noqa: PLR0913
        self,
        *,
        filepath: str,
        load_args: dict[str, Any] | None = None,
        save_args: dict[str, Any] | None = None,
        version: Version | None = None,
        credentials: dict[str, Any] | None = None,
        fs_args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Creates a new instance of ``ParquetDataset`` pointing to a concrete Parquet file
        on a specific filesystem.

        Args:
            filepath: Filepath in POSIX format to a Parquet file prefixed with a protocol like
                `s3://`. If prefix is not provided, `file` protocol (local filesystem) will be used.
                The prefix should be any protocol supported by ``fsspec``.
                It can also be a path to a directory. If the directory is
                provided then it can be used for reading partitioned parquet files.
                Note: `http(s)` doesn't support versioning.
            load_args: Additional options for loading Parquet file(s).
                Here you can find all available arguments when reading single file:
                https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.read_parquet.html
                Here you can find all available arguments when reading partitioned datasets:
                https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetDataset.html#pyarrow.parquet.ParquetDataset.read
                All defaults are preserved.
            save_args: Additional saving options for saving Parquet file(s).
                Here you can find all available arguments:
                https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.to_parquet.html
                All defaults are preserved. ``partition_cols`` is not supported.
            version: If specified, should be an instance of ``kedro.io.core.Version``.
                If its ``load`` attribute is None, the latest version will be loaded. If
                its ``save`` attribute is None, save version will be autogenerated.
            credentials: Credentials required to get access to the underlying filesystem.
                E.g. for ``GCSFileSystem`` it should look like `{"token": None}`.
            fs_args: Extra arguments to pass into underlying filesystem class constructor
                (e.g. `{"project": "my-project"}` for ``GCSFileSystem``).
                Defaults are preserved, apart from the `open_args_save` `mode` which is set to `wb`.
                Note that the save method requires bytes, so any save mode provided should include "b" for bytes.
            metadata: Any arbitrary metadata.
                This is ignored by Kedro, but may be consumed by users or external plugins.
        """
        _fs_args = deepcopy(fs_args or {})
        _fs_open_args_load = _fs_args.pop("open_args_load", {})
        _fs_open_args_save = _fs_args.pop("open_args_save", {})
        _credentials = deepcopy(credentials or {})

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

    def load(self) -> pd.DataFrame:
        load_path = str(self._get_load_path())
        if self._protocol == "file":
            # file:// protocol seems to misbehave on Windows
            # (<urlopen error file not on local host>),
            # so we don't join that back to the filepath;
            # storage_options also don't work with local paths
            return pd.read_parquet(load_path, **self._load_args)

        load_path = f"{self._protocol}{PROTOCOL_DELIMITER}{load_path}"
        return pd.read_parquet(
            load_path, storage_options=self._storage_options, **self._load_args
        )

    def save(self, data: pd.DataFrame) -> None:
        save_path = get_filepath_str(self._get_save_path(), self._protocol)

        if Path(save_path).is_dir():
            raise DatasetError(
                f"Saving {self.__class__.__name__} to a directory is not supported."
            )

        if "partition_cols" in self._save_args:
            raise DatasetError(
                f"{self.__class__.__name__} does not support save argument "
                f"'partition_cols'. Please use 'kedro.io.PartitionedDataset' instead."
            )

        with self._fs.open(save_path, **self._fs_open_args_save) as fs_file:
            data.to_parquet(fs_file, **self._save_args)

        self._invalidate_cache()

    def _exists(self) -> bool:
        try:
            load_path = get_filepath_str(self._get_load_path(), self._protocol)
        except DatasetError:
            return False

        return self._fs.exists(load_path)

    def _release(self) -> None:
        super()._release()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Invalidate underlying filesystem caches."""
        filepath = get_filepath_str(self._filepath, self._protocol)
        self._fs.invalidate_cache(filepath)

    def preview(self, nrows: int = 5) -> TablePreview:
        """
        Generate a preview of the dataset with a specified number of rows.

        Args:
            nrows: The number of rows to include in the preview. Defaults to 5.

        Returns:
            dict: A dictionary containing the data in a split format.
        """
        import pyarrow.parquet as pq  # noqa: PLC0415

        load_path = str(self._get_load_path())

        table = pq.read_table(
            load_path, columns=self._load_args.get("columns"), use_threads=True
        )[:nrows]
        data_preview = table.to_pandas()

        return data_preview.to_dict(orient="split")
