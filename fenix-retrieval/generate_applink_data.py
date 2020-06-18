#!/usr/bin/python3
import argparse
import csv
import git
import json
import urllib
import numpy as np
import pathlib
from redo import retry
import requests
import tempfile

try:
    from urllib.parse import urlencode
    from urllib.request import urlopen, urlretrieve
except ImportError:
    from urllib import urlencode, urlretrieve
    from urllib2 import urlopen


RETRY_SLEEP = 10
AD_QUERY = {
	"from":"task",
	"where":{"and":[
		{"regex":{"run.name":".*fenix.*"}},
		{"regex":{"run.name":".*perftest.*"}},
		{"in":{"repo.branch.name":["mozilla-central"]}}
	]},
	"select":["task.artifacts", "action.start_time"],
	"limit":100000
}


def csv_generation_parser():
	"""Parser for the CSV generation script."""
	parser = argparse.ArgumentParser("Run this tool to build CSVs containing Fenix data from some tasks " +
									 "running with the multi-commit paradigm in mozilla-central " +
									 "(must have perfherder data).")
	parser.add_argument("-t", "--test-name", type=str, default="applink",
						help="The name of the test to get data from (must exist in the task name). " +
						"Defaults to `applink`.")
	parser.add_argument("-d", "--device", type=str, choices=["p2", "g5"], default="p2",
						help="Device to get data from.")
	parser.add_argument("-c", "--cache-path", type=str, default=None,
						help="Path to a cache for perfherder artifacts (so you don't re-download them). " +
						"Disabled by default.")
	parser.add_argument("-r", "--fenix-repo", type=str, required=True,
						help="Path to a local Fenix github repo.")
	parser.add_argument("-o", "--output", type=str, default=None,
						help="Path to the output directory. Defaults to current working directory.")
	return parser


def query_activedata(query_json):
    """Used to run queries on active data."""
    active_data_url = "http://activedata.allizom.org/query"

    req = urllib.request.Request(active_data_url)
    req.add_header("Content-Type", "application/json")
    jsondata = json.dumps(query_json)

    jsondataasbytes = jsondata.encode("utf-8")
    req.add_header("Content-Length", len(jsondataasbytes))

    print("Querying Active-data...")
    response = urllib.request.urlopen(req, jsondataasbytes)
    print("Status:" + str(response.getcode()))

    data = json.loads(response.read().decode("utf8").replace("'", '"'))["data"]
    return data



def download_file(url, target, retry_sleep=RETRY_SLEEP, attempts=3):
    """Downloads a file, given an URL in the target path.

    The function will attempt several times on failures.
    """

    def _download_file(url, target):
        req = requests.get(url, stream=True, timeout=30)
        target_dir = target.parent.resolve()
        if str(target_dir) != "":
            target_dir.mkdir(exist_ok=True)

        with target.open("wb") as f:
            for chunk in req.iter_content(chunk_size=1024):
                if not chunk:
                    continue
                f.write(chunk)
                f.flush()
        return target

    return retry(
        _download_file,
        args=(url, target),
        attempts=attempts,
        sleeptime=retry_sleep,
        jitter=0,
    )


def build_csv(
		fenix_repo,
		test_name="applink",
		device_name="p2",
		output=None,
		cache_path=None
	):
	"""Generates a CSV file containing per-commit fenix data
	for a given test name.
	"""
	if cache_path:
		cache_path = pathlib.Path(cache_path)
		cache_path.mkdir(parents=True, exist_ok=True)
	else:
		cache_path = tempfile.mkdtemp()

	if output:
		output = pathlib.Path(output)
		output.mkdir(parents=True, exist_ok=True)
	else:
		output = pathlib.Path(".")

	# Initialize the git directory now before the long steps below
	fenix = git.Repo(fenix_repo)

	print(f"Generating data for {test_name} on the {device_name} device...")

	## Get the AD data
	AD_QUERY["where"]["and"].extend([
		{"regex": {"run.name": ".*%s.*" % test_name}},
		{"regex": {"run.name": ".*-%s-.*" % device_name}}
	])
	data = query_activedata(AD_QUERY)

	allph = []
	for artifacts in data["task.artifacts"]:
		if not artifacts: continue
		for artifact in artifacts:
			if not artifact: continue

			ph = artifact["url"].split("/")[-1]
			if "perfherder" not in ph or ph.startswith("perfherder"): continue

			allph.append((artifact["url"], ph, ph.split("-")[0]))
	
	## Download the perfherder data and get its commit date
	nallph = []
	for url, file, rev in allph:
		fp = pathlib.Path(cache_path, file)
		if not fp.exists():
			print(f"Downloading to {fp}" )
			download_file(url, fp)
		
		with fp.open() as f:
			phd = json.load(f)

		# sanity checks
		if ("suites" not in phd or
			len(phd["suites"]) == 0 or
			"value" not in phd["suites"][0]):
			print("Bad data, skipping...")
			continue

		try:
			nallph.append((fenix.commit(rev).committed_date, phd["suites"][0]["value"], rev))
		except ValueError:
			# Some commits don't exist which is really weird - I don't
			# understand how there's a build for them when we can't find them
			# in the fenix repo.
			print("Failed to find an actual commit for %s" % rev)

	# Sort the data by time
	allphs = sorted(nallph, key=lambda x: x[0])

	## Store as a CSV
	csvfile = pathlib.Path(output, f"{test_name}-{device_name}.csv")
	with csvfile.open("w") as f:
		writer = csv.writer(f)
		writer.writerow(["times", "data", "revision"])
		writer.writerows(allphs)
	print(f"Finished generation. Data contained in {str(csvfile)}")


if __name__=="__main__":
	args = csv_generation_parser().parse_args()
	build_csv(
		args.fenix_repo,
		args.test_name,
		args.device,
		args.output,
		args.cache_path
	)