# Google Stitch master prompt — EarthScope

Design a complete responsive Russian-language web application for EarthScope, a scientific climate exploration workstation for NASA Space Apps. Use the attached inventory files as the functional source of truth. Reference screenshots show real working states and inputs. Compose a new information architecture and visual hierarchy. Every visible UI label, help message, empty state, chart axis, legend and error should be in Russian. Use real technical terms such as NASA POWER, MERRA‑2, UTC, LST, WGS84, HAC and BY with Russian explanations.

## Product and layout

Help a first-time user select a point, climate variable and years, run an analysis, and understand the outcome. Give the research controls and primary action a clear, compact entry point. Keep the selection and the result visibly related. Provide a strong system for desktop, tablet and mobile. You may redesign navigation, use split views, contextual panels or progressive disclosure, and change where each action lives. Preserve all workflows and capabilities in the inventories. Prefer a restrained professional GIS/scientific palette, typographic hierarchy, sparse borders, minimal motion and semantic chart colors. Keep touch targets and keyboard focus obvious. Allow scrolling inside large tables without horizontal page overflow.

## Required functional scope

- Single-point trends and paired A/B comparison, including selectable active point, shared time period, series overlay or common-scale side-by-side plots, absolute/anomaly view and editable common baseline.
- Climate anomalies with selectable years and separate reference period including the 1991–2020 preset. Seasonal analysis with four seasons, twelve months and an all-seasons option.
- Threshold extremes with all three event types and optional A/B. Heatwaves as consecutive strict Tmax exceedances with editable minimum duration, three annual series, year selection and dated episodes.
- World map with temperature/precipitation, trend/BY, smooth/native views, appropriate legends, initial auto-load, explicit map controls, original cell selection by map and coordinates, cell details, annual table and PDF.
- Climate Profile showing seven possible computed sections, partial error reporting, expandable charts and metadata, adjustable reference range and thresholds, unified CSV/PDF. Heatwaves are explicitly unavailable within this aggregate profile and remain available in their own study.
- Detective with all eight question cards and distinct coordinates, time/parameters and results states. Preserve back, repeat, matching saved result and reset behavior. Reuse the relevant result capabilities.
- Contextual export actions with loading, cancellation where available and error recovery. Preserve CSV/PDF by mode and PNG where Plotly provides it. PDF represents the analyzed request. A/B PDF excludes client display choices and the revised anomaly baseline.

## Science and interaction constraints

Distinguish MERRA‑2 gridded reanalysis from local observations. Keep UTC point workflows distinct from the annual LST world product. Show units, periods, source, coordinate precision, missingness, observation coverage and uncertainty where available. Treat A/B slope comparison as descriptive. Never infer causation or equate nonsignificance with zero change. Keep missing values distinct from zero. Label map smoothing and the BY neighborhood percentage scale; the cell inspector always shows original-cell statistics. Avoid treating the visual categories as evidence beyond their stated method. Do not invent confidence bands, scientific results, user accounts, report history, data layers, region controls or backend features. Use existing real examples from the screenshots or explicitly labeled placeholders.

Use map clicks and editable coordinates together. Pan and zoom remain separate from requests and from changing the selection. Respect prepared vs analyzed parameters and avoid changing a report when a user edits a draft. Include useable empty, invalid, loading, cancelled, partial, error, stale-result and successful states where supported. Include a keyboard-friendly path to all important values. Do not require hover to read the year or exact result. Keep validation, research limitations and exports discoverable.

Deliver a coherent component system and a set of desktop and mobile research screens and state variants covering the screen inventory. Annotate which functional controls are retained and show the supported navigation between workflows. The app itself stays Russian, even if your design annotations are in English.
