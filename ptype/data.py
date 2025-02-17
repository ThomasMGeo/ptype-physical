import os
import glob
import logging
import numpy as np
import pandas as pd
from tqdm import tqdm
from joblib import Parallel, delayed
from sklearn.preprocessing import (
    StandardScaler,
    MinMaxScaler,
    OneHotEncoder,
    LabelEncoder,
    RobustScaler,
    QuantileTransformer,
)
from bridgescaler.group import GroupMinMaxScaler, GroupRobustScaler, GroupStandardScaler
from sklearn.model_selection import GroupShuffleSplit


logger = logging.getLogger(__name__)


def load_ptype_data(
    data_path,
    source,
    train_start="20130101",
    train_end="20181108",
    val_start="20181109",
    val_end="20200909",
    test_start="20200910",
    test_end="20210501",
):
    """
    Load Precip Type data
    Args:
        data_path (str): Path to data
        source (str): Precip observation source. Supports 'ASOS' or 'mPING'.
        train_start (str): Train split start date (format yyyymmdd).
        train_end (str): Train split end date (format yyyymmdd).
        val_start (str): Valid split start date (format yyyymmdd).
        val_end (str): Valid split end date (format yyyymmdd).
        test_start (str): Test split start date (format yyyymmdd).
        test_end (str): Test split end date (format yyyymmdd).
    Returns:
    Dictionary of Pandas dataframes of training / validation / test data
    """

    dates = sorted([x[-16:-8] for x in os.listdir(data_path)])

    data = {}
    data["train"] = dates[dates.index(train_start) : dates.index(train_end) + 1]
    data["val"] = dates[dates.index(val_start) : dates.index(val_end) + 1]
    data["test"] = dates[dates.index(test_start) : dates.index(test_end) + 1]

    for split in data.keys():
        dfs = []
        for date in tqdm(data[split], desc=f"{split}"):
            f = f"{source}_rap_{date}.parquet"
            dfs.append(pd.read_parquet(os.path.join(data_path, f)))
        data[split] = pd.concat(dfs, ignore_index=True)

    return data


def load_ptype_uq(conf, data_split=0, verbose=0, drop_mixed=False):

    # Load
    df = pd.read_parquet(conf["data_path"])

    # Drop mixed cases
    if drop_mixed:
        logger.info("Dropping data points with mixed observations")
        c1 = df["ra_percent"] == 1.0
        c2 = df["sn_percent"] == 1.0
        c3 = df["pl_percent"] == 1.0
        c4 = df["fzra_percent"] == 1.0
        condition = c1 | c2 | c3 | c4
        df = df[condition].copy()

    # QC-Filter
    qc_value = str(conf["qc"])
    cond1 = df[f"wetbulb{qc_value}_filter"] == 0.0
    cond2 = df["usa"] == 1.0
    dg = df[cond1 & cond2].copy()

    dg["day"] = dg["datetime"].apply(lambda x: str(x).split(" ")[0])
    dg["id"] = range(dg.shape[0])

    # Select test cases
    test_days_c1 = dg["day"].isin(
        [day for case in conf["case_studies"].values() for day in case]
    )
    test_days_c2 = dg["day"] >= conf["test_cutoff"]
    test_condition = test_days_c1 | test_days_c2

    # Partition the data into trainable-only and test-only splits
    train_data = dg[~test_condition].copy()
    test_data = dg[test_condition].copy()

    # Make N train-valid splits using day as grouping variable, return "data_split" split
    gsp = GroupShuffleSplit(
        n_splits=conf["ensemble"]["n_splits"],
        random_state=conf["seed"],
        train_size=conf["train_size1"],
    )
    splits = list(gsp.split(train_data, groups=train_data["day"]))

    train_index, valid_index = splits[data_split]
    train_data, valid_data = (
        train_data.iloc[train_index].copy(),
        train_data.iloc[valid_index].copy(),
    )

    size = df.shape[0]
    logger.info("Train, validation, and test fractions:")
    logger.info(
        f"{train_data.shape[0]/size}, {valid_data.shape[0]/size}, {test_data.shape[0]/size}"
    )
    data = {"train": train_data, "val": valid_data, "test": test_data}

    return data


def load_ptype_data_subset(
    data_path, source, start_date, end_date, n_jobs=1, verbose=1
):
    """
    Load a single range of dates from the mPING or ASOS parquet files into memory. Supports parallel loading with joblib.

    Args:
        data_path: Path to appropriate p-type directory containing parquet files.
        source: "mPING" or "ASOS"
        start_date: Pandas-supported Date string for first day in time range (inclusive)
        end_date: Pandas supported Date string for last day in time range (inclusive)
        n_jobs: Number of parallel processes to use for data loading (default 1)
        verbose: verbose level
    Returns:
        data: Pandas DataFrame containing all sounding and p-type data from start_date to end_date.

    """
    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date)
    data_files = sorted(os.listdir(data_path))
    all_dates = pd.DatetimeIndex([x[-16:-8] for x in data_files])
    selected_dates = all_dates[
        (all_dates >= start_timestamp) & (all_dates <= end_timestamp)
    ]
    dfs = []
    if n_jobs == 1:
        for date in tqdm(selected_dates):
            date_str = date.strftime("%Y%m%d")
            filename = f"{source}_rap_{date_str}.parquet"
            dfs.append(pd.read_parquet(os.path.join(data_path, filename)))
    else:
        date_strs = selected_dates.strftime("%Y%m%d")
        dfs = Parallel(n_jobs=n_jobs, verbose=verbose)(
            [
                delayed(pd.read_parquet)(
                    os.path.join(data_path, f"{source}_rap_{date_str}.parquet")
                )
                for date_str in date_strs
            ]
        )
    data = pd.concat(dfs, ignore_index=True)
    return data


def load_ptype_data_day(conf, data_split=0, verbose=0, drop_mixed=False):

    if "parquet" in conf["data_path"]:
        df = pd.read_parquet(conf["data_path"])
        # cond1 = (df["datetime"].apply(lambda x: str(x).split(" ")[0]) < "2020-07-01")
        # cond2 = (df[["usa", "wetbulb5.0_filter"]].sum(axis = 1) > 0.0)
        cond2 = df["wetbulb5.0_filter"] == 0.0
        cond3 = df["usa"] == 1.0
        df = df[cond2 & cond3].copy()
        print(df.shape)

    elif not os.path.isfile(os.path.join(conf["data_path"], "cached.parquet")):
        df = pd.concat(
            [
                pd.read_parquet(x)
                for x in tqdm(glob.glob(os.path.join(conf["data_path"], "*.parquet")))
            ]
        )
        df.to_parquet(os.path.join(conf["data_path"], "cached.parquet"))

    else:
        df = pd.read_parquet(os.path.join(conf["data_path"], "cached.parquet"))

    # Drop mixed cases
    if drop_mixed:
        logger.info("Dropping data points with mixed observations")
        c1 = df["ra_percent"] == 1.0
        c2 = df["sn_percent"] == 1.0
        c3 = df["pl_percent"] == 1.0
        c4 = df["fzra_percent"] == 1.0
        condition = c1 | c2 | c3 | c4
        df = df[condition].copy()

    # Split and preprocess the data
    df["day"] = df["datetime"].apply(lambda x: str(x).split(" ")[0])
    df["id"] = range(df.shape[0])
    test_days = [day for case in conf["case_studies"].values() for day in case]
    test_days_c = df["day"].isin(test_days)

    # Need the same test_data for all trained models (data and model ensembles)
    gsp = GroupShuffleSplit(
        n_splits=conf["n_splits"],
        random_state=conf["seed"],
        train_size=conf["train_size1"],
    )
    splits = list(gsp.split(df[~test_days_c], groups=df[~test_days_c]["day"]))
    train_index, test_index = splits[0]
    train_data, test_data = (
        df[~test_days_c].iloc[train_index].copy(),
        df[~test_days_c].iloc[test_index].copy(),
    )
    test_data = pd.concat([test_data, df[test_days_c].copy()])

    # Make N train-valid splits using day as grouping variable, return "data_split" split
    gsp = GroupShuffleSplit(
        n_splits=conf["n_splits"],
        random_state=conf["seed"],
        train_size=conf["train_size2"],
    )
    splits = list(gsp.split(train_data, groups=train_data["day"]))

    train_index, valid_index = splits[data_split]
    train_data, valid_data = (
        train_data.iloc[train_index].copy(),
        train_data.iloc[valid_index].copy(),
    )

    if verbose:
        size = df.shape[0]
        logger.info("Train, validation, and test fractions:")
        logger.info(
            f"{train_data.shape[0]/size}, {valid_data.shape[0]/size}, {test_data.shape[0]/size}"
        )

    data = {"train": train_data, "val": valid_data, "test": test_data}

    return data


def preprocess_data(
    data,
    input_features,
    output_features,
    scaler_type="standard",
    encoder_type="onehot",
    groups=[],
    seed=1000,
):
    """
    Function to select features and scale data for ML
    Args:
        data (dictionary of dataframes for training and validation data):
        input_features (list): Input features
        output_feature (list): Output feature
        scaler_type: Type of scaling to perform (supports "standard" and "minmax")
        encoder_type: Type of encoder to perform (supports "label" and "onehot")

    Returns:
        Dictionary of scaled and one-hot encoded data, dictionary of scaler objects
    """
    groupby = len(groups)

    scalar_obs = {
        "normalize": MinMaxScaler() if not groupby else GroupMinMaxScaler(),
        "symmetric": MinMaxScaler((-1, 1))
        if not groupby
        else GroupMinMaxScaler((-1, 1)),
        "standard": StandardScaler() if not groupby else GroupStandardScaler(),
        "robust": RobustScaler() if not groupby else GroupRobustScaler(),
        "quantile": QuantileTransformer(
            n_quantiles=1000, random_state=seed, output_distribution="normal"
        ),
        "quantile-uniform": QuantileTransformer(
            n_quantiles=1000, random_state=seed, output_distribution="uniform"
        ),
    }
    scalers, scaled_data = {}, {}
    scalers["input"] = scalar_obs[scaler_type]
    scalers["output_label"] = LabelEncoder()
    if encoder_type == "onehot":
        scalers["output_onehot"] = OneHotEncoder(sparse=False)

    if groupby and "quantile" not in scaler_type:
        scaled_data["train_x"] = pd.DataFrame(
            scalers["input"].fit_transform(
                data["train"][input_features], groups=groups
            ),
            columns=input_features,
        )
    else:
        scaled_data["train_x"] = pd.DataFrame(
            scalers["input"].fit_transform(data["train"][input_features]),
            columns=input_features,
        )
    scaled_data["val_x"] = pd.DataFrame(
        scalers["input"].transform(data["val"][input_features]), columns=input_features
    )
    scaled_data["test_x"] = pd.DataFrame(
        scalers["input"].transform(data["test"][input_features]), columns=input_features
    )
    if "left_overs" in data:
        scaled_data["left_overs_x"] = pd.DataFrame(
            scalers["input"].transform(data["left_overs"][input_features]),
            columns=input_features,
        )

    scalers["output_label"] = LabelEncoder()
    scaled_data["train_y"] = scalers["output_label"].fit_transform(
        np.argmax(data["train"][output_features].to_numpy(), 1)
    )
    scaled_data["val_y"] = scalers["output_label"].transform(
        np.argmax(data["val"][output_features].to_numpy(), 1)
    )
    scaled_data["test_y"] = scalers["output_label"].transform(
        np.argmax(data["test"][output_features].to_numpy(), 1)
    )
    if "left_overs" in data:
        scaled_data["left_overs_y"] = scalers["output_label"].transform(
            np.argmax(data["left_overs"][output_features].to_numpy(), 1)
        )

    if encoder_type == "onehot":
        scalers["output_onehot"] = OneHotEncoder(sparse=False)
        scaled_data["train_y"] = scalers["output_onehot"].fit_transform(
            np.expand_dims(scaled_data["train_y"], 1)
        )
        scaled_data["val_y"] = scalers["output_onehot"].transform(
            np.expand_dims(scaled_data["val_y"], 1)
        )
        scaled_data["test_y"] = scalers["output_onehot"].transform(
            np.expand_dims(scaled_data["test_y"], 1)
        )
        if "left_overs" in data:
            scaled_data["left_overs_y"] = scalers["output_onehot"].transform(
                np.expand_dims(scaled_data["left_overs_y"], 1)
            )

    return scaled_data, scalers


def reshape_data_1dCNN(
    data, base_variables=["TEMP_C", "T_DEWPOINT_C", "UGRD_m/s", "VGRD_m/s"], n_levels=67
):
    arr = np.zeros(shape=(data.shape[0], n_levels, len(base_variables))).astype(
        "float32"
    )
    for i, var in enumerate(base_variables):
        profile_vars = [x for x in list(data.columns) if var in x]
        arr[:, :, i] = data[profile_vars].values.astype("float32")
    return arr
