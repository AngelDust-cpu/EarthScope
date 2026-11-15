"""Prepare the 1990–2025 EarthScope world layer from NASA POWER Zarr v2.

Run with the backend environment, plus numcodecs for this offline step only.
The deployed app reads the resulting NPZ and needs no Zarr dependencies.
"""
import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import os
from pathlib import Path

import httpx
import numpy as np
from numcodecs import get_codec

URL = 'https://nasa-power.s3.us-west-2.amazonaws.com/merra2/spatial/power_merra2_annual_spatial_lst.zarr/'
YEARS = tuple(range(1990, 2026))
PARAMETERS = {'temperature': 'T2M', 'precipitation': 'PRECTOTCORR'}
ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get('EARTHSCOPE_CACHE_DIR', ROOT / '.earthscope-cache')) / 'global'


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=35) as client, ThreadPoolExecutor(max_workers=4) as pool:
        response = client.get(URL + '.zmetadata')
        response.raise_for_status()
        metadata = response.json()['metadata']

        def read(name, chunk):
            schema = metadata[f'{name}/.zarray']
            response = client.get(f'{URL}{name}/{chunk}')
            response.raise_for_status()
            values = np.frombuffer(get_codec(schema['compressor']).decode(response.content), dtype=schema['dtype'])
            for codec in reversed(schema['filters'] or []):
                values = get_codec(codec).decode(values)
            return values.reshape(schema['chunks'])

        epoch = date.fromisoformat(metadata['time/.zattrs']['units'].split()[2])
        dates = [epoch + timedelta(days=int(day)) for day in read('time', '0')]
        indices = [dates.index(date(year, 12, 31)) for year in YEARS]
        lat, lon = read('lat', '0').ravel(), read('lon', '0').ravel()
        np.testing.assert_allclose(lat, np.arange(-90, 90.5, .5), atol=1e-8)
        np.testing.assert_allclose(lon, np.arange(-180, 180, .625), atol=1e-8)
        arrays = {}
        for variable, parameter in PARAMETERS.items():
            units = metadata[f'{parameter}/.zattrs']['units']
            assert units == ('C' if variable == 'temperature' else 'mm/day')
            results = pool.map(lambda index: read(parameter, f'{index}.0.0')[0], indices)
            annual = np.round(np.stack(list(results)).astype('float64'), 2)
            if variable == 'precipitation':
                annual = annual * np.array([365 + calendar.isleap(year) for year in YEARS])[:, None, None]
            if not np.isfinite(annual).all():
                raise RuntimeError(f'{parameter} has missing values in the requested years')
            arrays[variable] = annual.astype('float32')
            print(f'{variable}: {annual.shape}, {annual.min():.2f} to {annual.max():.2f}', flush=True)
    with (CACHE / 'annual.tmp').open('wb') as output:
        np.savez_compressed(output, years=YEARS, lat=lat, lon=lon, version=metadata['.zattrs']['version'], **arrays)
    os.replace(CACHE / 'annual.tmp', CACHE / 'annual.npz')


if __name__ == '__main__':
    main()
