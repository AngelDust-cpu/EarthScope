# EarthScope demo and submission draft

EarthScope helps students investigate how temperature and precipitation at a chosen location have changed. It combines a world map, annual time series, and transparent statistics so users can see how much a variable changed and how uncertain that estimate is. Each analysis includes real NASA POWER data provenance and a CSV of the displayed values.

## Two-minute demo

Before presenting, run both comparisons with the cache enabled. Use the defaults: A Bishkek (42.870, 74.600), B Reykjavík (64.147, −21.942), 1990–2025. Return to a fresh page so the audience sees how analysis starts.

1. **0–25 seconds.** From the home page, choose **Начать исследование**, then **Сравнение A / B**. Show the two points on the map and click **Провести анализ** for temperature. For a rehearsal, open `/research/compare` directly.
2. **25–55 seconds.** Point out the two fitted slopes: +0.373 and +0.130 °C per decade. The paired difference is +0.242 °C per decade. Explain that this is an estimate from 36 shared years; the app does not test whether the difference is statistically significant.
3. **55–90 seconds.** Switch to anomalies to show each point relative to its own average over the same baseline years, 1990–1999. Choose precipitation and run again. Show annual totals in mm and the paired difference of +41.40 mm per decade.
4. **90–120 seconds.** Download **Скачать CSV · A и B** and show NASA POWER source metadata. Explain that MERRA-2 is a set of grid estimates and the individual OLS intervals assume independent residuals. [Scientific method](SCIENTIFIC_METHOD.md) · [Demo verification](DEMO-VERIFICATION.md)

## Sources and limitations

For a 2.3 demo, use **Климатические аномалии** with Bishkek and the 1991–2020 reference. Explain that each bar is the difference between the annual value and a reference shared by all displayed years; the dashed line marks zero. In **Сезонный анализ**, choose **Все четыре сезона** and show spring's estimated temperature slope for the default years. Explain that winter 2020 begins in December 2019 and that the season ranking is descriptive. Both questions are also available under **Детектив**. [Verified 2.3 values](DEMO-VERIFICATION.md#earthscope-23--anomalies-and-seasons)

EarthScope uses NASA POWER Daily Point MERRA-2 temperature at 2 meters (T2M) and corrected precipitation (PRECTOTCORR). NASA POWER provides free public access without an account. The basemap uses Natural Earth public-domain data. Sources checked November 14, 2026. [NASA POWER Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) · [NASA POWER data overview](https://power.larc.nasa.gov/resources/power-one-pager.pdf) · [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/)

Data credit: NASA Langley Research Center's POWER project, funded through NASA's Earth Science Division. Both demo points were retrieved through POWER Daily API v2.10.3 on November 14, 2026. [POWER referencing guide](https://power.larc.nasa.gov/docs/referencing/)

The selected coordinate represents a gridded reanalysis estimate, which can differ from local station conditions. Missing days exclude the entire year. The linear model uses at least five complete years. Its nominal interval and significance test do not adjust for serial dependence or for testing many places or time windows. Results are descriptive. The app needs NASA connectivity for uncached queries. Prewarm the exact demo queries before presenting if connectivity is uncertain.
