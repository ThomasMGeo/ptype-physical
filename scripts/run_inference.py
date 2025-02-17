import argparse
import os
import yaml
import pandas as pd
from ptype.inference import download_data, load_data, convert_and_interpolate
from ptype.inference import load_model, transform_data, grid_predictions, save_data
import itertools
from multiprocessing import Pool
from dask.distributed import Client
from dask_jobqueue import PBSCluster


def main(config, username, date, forecast_hour):

    out_path = config["out_path"].replace("username", username)
    nwp_model = config["model"]
    model, transformer = load_model(model_path=config["ML_model_path"],
                                    model_file=config["model_file"],
                                    input_scaler_file=config["input_scaler_file"],
                                    output_scaler_file=config["output_scaler_file"])
    file = download_data(date=date,
                         model=config["model"],
                         product=config["variables"]["model"][nwp_model]["product"],
                         save_dir=out_path,
                         forecast_hour=forecast_hour)

    ds, df, surface_vars = load_data(var_dict=config["variables"]["model"][nwp_model],
                                     file=file,
                                     model=nwp_model,
                                     drop=config["drop_input_data"])

    data = convert_and_interpolate(data=df,
                                   surface_data=surface_vars,
                                   pressure_levels=ds["isobaricInhPa"],
                                   height_levels=config["height_levels"])

    x_data = transform_data(input_data=data,
                            transformer=transformer)

    predictions = model.predict(x_data)
    gridded_preds = grid_predictions(data=ds,
                                    preds=predictions)
    save_data(dataset=gridded_preds,
              out_path=out_path,
              date=date,
              model=config["model"],
              forecast_hour=forecast_hour,
              save_format=config["save_format"])
    del ds, df, surface_vars, data, x_data, predictions, gridded_preds


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="Path to config file")
    args = parser.parse_args()
    with open(args.config) as config_file:
        config = yaml.safe_load(config_file)
    username = os.environ.get('USER')

    dates = pd.date_range(start=config["dates"]["start"],
                          end=config["dates"]["end"],
                          freq=config["dates"]["frequency"])

    forecast_hours = range(config["forecast_range"]["start"],
                           config["forecast_range"]["end"] + config["forecast_range"]["interval"],
                           config["forecast_range"]["interval"])

    main_args = itertools.product([config], [username], dates, forecast_hours)

    if config["use_dask"]:

        cluster = PBSCluster(**config["dask_params"]["PBS"])
        client = Client(cluster)
        cluster.scale(jobs=config["dask_params"]["n_jobs"])
        print(f"Use this link to monitor the workload: {cluster.dashboard_link}")
        tasks = []
        for arguments in main_args:
            tasks.append(client.submit(main, *arguments))
        _ = [tasks[i].result() for i in range(len(tasks))]

    else:
        with Pool(processes=config["n_processors"]) as pool:
            pool.starmap(main, main_args)
