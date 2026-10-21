# Run VO₂ field-test audit (#107)

Started 2026-10-20 and finalised 2026-10-21.

## Status and decisions (authoritative)

This section is the record. Where the analysis further down disagrees with it, this section wins. Text marked *superseded* below is kept only as history.

**Verified equation.** George JD, Vehrs PR, Allsen PE, Fellingham GW, Fisher AG. *Med Sci Sports Exerc* 1993;25(3):401-406, PMID 8455458. Taken from **Table 2, "1.5-Mile Run (N = 49)", relative VO₂max column, p. 404**, in the full-text PDF (https://www.researchgate.net/profile/Gilbert-Fellingham/publication/14744769_VO2_max_estimation_from_a_submaximal_1-mile_track_jog_for_fit_college_age_individuals/links/5a0f209baca27287ce273412/VO2-max-estimation-from-a-submaximal-1-mile-track-jog-for-fit-college-age-individuals.pdf):

`VO₂max (ml·kg⁻¹·min⁻¹) = 88.02 + 3.716·GENDER − 0.1656·BODY MASS (kg) − 2.767·ELAPSED TIME (min)`

- **Units:** body mass in kg; elapsed time in minutes, which the Table 1 footnote states.
- **Sex coding (an inference):** the paper prints "0 = female; 1 = male" only for its 1-mile jog model. For the 1.5-mile model the same coding is inferred, from the positive coefficient and the higher VO₂max of the male participants. The owner accepted that inference for this display-only release (2026-10-21), and it stays labelled as an inference.
- **Pounds:** converted exactly (1 lb = 0.45359237 kg) and run through the kg coefficient. There is no separate lb equation.
- **Error and population:** cross-validated SEE 3.9 ml·kg⁻¹·min⁻¹ (N = 47, r_adj 0.82). The study population was relatively fit college students aged 18–29.
- **Owner's reproduction (2026-10-21):** 46.2883 for a 163 lb man running 12:00, and 40.7295 for a 60 kg woman running 13:30.

**Decisions (owner, 2026-10-21). B2 is an estimate-only feature, and aerobic-capacity seeding is out of this release.**
- **D1, protocol:** the standalone, maximal 1.5-mile run, under a new protocol code and version (`run_1p5mi_time_trial`, model `george1993_1p5mi_run` v1). The old definition and all its history are preserved. The corrected calculation is enabled; its coefficients are verified as above.
- **D2, inputs and storage:**
  - Time, body mass and the model's sex input are required only to calculate an estimate.
  - Age at performance decides population eligibility.
  - There are no defaults and no inferred sex. Missing inputs still save the run and still let onboarding finish.
  - The inputs and the formula version are recorded on each assessment's `protocol_metadata`. There is no profile sex field and no migration.
- **D3, population:** every result is display-only. A result outside the study population is labelled "extrapolated and unvalidated". Age alone does not establish the study's other conditions.
- **D4, uncertainty:** SEE 3.9 is kept as an error scale in VO₂ units. It is not a calibrated variance on the app's capacity scale, so the estimate gets **no capacity authority**. An estimate is never relabelled as measured.
- **D5, the 300 m:** removed from this VO₂ path. No fatigue metric is added in this release.
- **D6, presentation:** the unsourced grades are removed. The estimate reads "Estimated VO₂max — not used for your plan." Guest sample values are visibly labelled as sample data.
- **Release requirement:** these records must not change capacity, prescription eligibility, or any reader of demonstrated achievement. Historical rows stay unchanged, and there is no historical reseeding.

**Superseded below:** anything recommending capacity authority, gating authority by population, carrying the SEE into a capacity variance, or changing the old seed definition. The same goes for every statement that the coefficients are unverified.

---

## Original analysis (2026-10-20, read at `origin/main` `fb43aa4`)

Read at `origin/main` `fb43aa4`. Nothing in the code was changed. Each claim is tagged:
- **[source]**: a source listed in §8;
- **[repo file:line]**: a line in the repository;
- **[inferred]**: worked out from the above, with the reasoning shown (the computed numbers come from a calculation script in the audit's scratch folder);
- **[not found]**: looked for and not found.

**Owner decision (2026-10-20, B2 = A):** keep current behaviour while auditing. Before onboarding gains authority over aerobic state, the audit must establish protocol, inputs, applicable population and capacity mapping. No historical reseeding.

## Summary
1. **The equation.** `legacy.py:62-64` (88.02 / 0.1656 / 2.76 / 3.716) is the George et al. 1993 1.5-mile run model (MSSE 25(3):401-406), not Larsen 2002.
2. **The study.** George 1993 tested 149 "relatively fit" college students aged 18-29 (VO₂max 47.7 ± 6.3). The 1.5-mile model was fit on 50 of them, from run time, body mass and sex. Validation: r_adj 0.90, SEE 2.8. Cross-validation: r_adj 0.82, **SEE 3.9**.
3. **Coefficients** *(superseded; now verified, see the status section)*. The original analysis worked from secondary sources. The full text later confirmed the kg form, with a time coefficient of 2.767 (not 2.76).
4. **The legacy implementation is wrong.**
   - It applies the per-kg coefficient to 163, which treats every athlete as 163 kg. That is a −14.75 ml/kg/min bias even if 163 lb was meant.
   - Sex is hard-coded male, which overestimates every woman by +3.7.
   - Age and sex are accepted in the request and ignored.
   - Example: a 163 lb man running 1.5 mi in 12:00 gets 31.6; the correct value is 46.4.
5. **The grade tables are unsourced.**
   - The VO₂ table is about one point below the "36-45" column of an unsourced table, traced to ACE / Golding 1986.
   - The 1.5-mile result table has no published source that could be found.
   - Neither table comes from Cooper or ACSM. The authoritative age-by-sex source is the Cooper Institute's 2013 norms.
6. **The 300 m.** The 300 m + 1.5 mi pairing comes from the Hinshaw | Sherwood program (a log-exponent fatigue factor, norm about 20%) and from Cooper / law-enforcement test batteries, which score the two runs separately. No published equation uses a 300 m time to estimate VO₂.
7. **The legacy fatigue factor.** It uses 2400 m instead of 2414 m for 1.5 mi. Its ±5% "Balanced" band labels almost everyone "Endurance-biased". The seeded Hinshaw 400 m→mile formula measures a different quantity.
8. **Effect on state.** For a fresh athlete (aerobic capacity 300, variance 0.85), one observation moves capacity 0.815 of the way to the measured value. 1 ml/kg/min of error is about 11.8 aerobic units, so SEE 3.9 is about ±46 units (±7% of the 0-650 scale). The resulting posterior variance (0.08) is lower than the validated-benchmark seed tier.
9. **The current bias, if it were wired in.** With legacy's bias and bidirectional authority, the 163 lb man at 12:00 would drop from 300 to about 134. Through `onboarding_onramp` the effect is capped at `initialize_prior` plus a floor, so a low estimate writes nothing.
10. **Recommendation** *(superseded by D1–D6)*.
    - Use a maximal 1.5-mile run, and collect body mass, sex and date of birth, with no defaults.
    - Give the result capacity authority only within the George population.
    - Label it `protocol_grade_estimate` and carry SEE 3.9 into the measurement variance.
    - Keep the onramp collection mode.
    - Remove the 300 m from VO₂.
    - Relabel or hide the guest tile.
    - No historical reseeding.

## Primary-source verification (2026-10-21)

The full text of George et al. 1993 was read from an OCR copy of the journal PDF. The PDF is hosted on ResearchGate by co-author G. W. Fellingham. The relevant table is **Table 2, "1.5-Mile Run (N = 49)", relative VO₂max column (p. 404)**:

`VO₂max (ml·kg⁻¹·min⁻¹) = 88.02 + 3.716·GENDER − 0.1656·BODY MASS (kg) − 2.767·ELAPSED TIME (min)` [source]

- **Units:**
  - Body mass is in kg; the row label is "Body mass (kg)".
  - Time is in minutes; the Table 1 footnote says "elapsed time … expressed as minutes".
  - There is no heart-rate term.
- **Coefficients versus the code:** the time coefficient is **2.767**. The "2.76" in `legacy.py:64` and in some secondary sources is a rounding. The lb form (−0.0753/lb) is a later derivation; the exact conversion is 0.1656 × 0.45359237 = 0.07512 per lb.
- **Gender coding:** the paper prints "0 = female; 1 = male" only for its 1-mile jog model. For the 1.5-mile model the same coding is **inferred**, from the positive coefficient and the higher male VO₂max.
- **Statistics:**
  - Validation: N = 49, r_adj 0.90, SEE 2.8.
  - Cross-validation: N = 47, r_adj 0.82, SEE 3.9 (8.0% of VO₂max).
  - The abstract's "N = 50" for the regression conflicts with the full text's 49; 49 is used here.
- **Protocol:** "run the prescribed distance as fast as possible", self-paced, on measured indoor and outdoor tracks, at least 48 h away from the other tests.
- **Population:** 149 relatively fit college students aged 18–29. The authors caution against use outside 18–29 and say the equation "appears best suited for individuals who are accustomed to sustained high intensity exercise."
- **Section 1.3 above is superseded.** The kg form with 2.767 is now [source], not [secondary].

## Owner decisions (2026-10-21, B2 build): the same as the status section above

B2 is estimate-only. Aerobic-capacity seeding is out of this release.

- **D1, protocol:** the standalone 1.5-mile run. It gets a new protocol code and version, and the old definition and its history are preserved. The corrected calculation is enabled only after the coefficients are verified, which this section does.
- **D2, inputs:**
  - Time, body mass and the model's sex input are required only to calculate the estimate. Age at performance decides eligibility.
  - There are no defaults and no inferred sex. Missing inputs still save the run and still complete onboarding.
  - The inputs and the formula version are recorded on each assessment's `protocol_metadata`. There is no profile sex field and no migration.
- **D3, population:** every result is display-only. Results outside the study population are labelled extrapolated and unvalidated. Age alone does not establish the study's other conditions.
- **D4, uncertainty:** the SEE is kept as an error scale in VO₂ units. It is not a calibrated variance on the capacity scale, and the estimate gets no capacity authority. An estimate is never relabelled as measured.
- **D5:** the 300 m is removed from the VO₂ path. No fatigue metric is added.
- **D6:** the unsourced grades are removed. The estimate reads "Estimated VO₂max — not used for your plan." Guest sample values stay labelled as sample data.

## 1. The VO₂max equation

### 1.1 Which equation it is
- `legacy.py:62-64` computes `88.02 − 0.1656·163 − 2.76·t_min + 3.716·1` [repo legacy.py:62-64].
- This is the 1.5-mile run model from George, Vehrs, Allsen, Fellingham & Fisher (1993) [S1].
- It is not the Larsen 2002 submaximal walk/jog/run equation at RPE 13, which is `65.404 + 7.707·G − 0.159·BM(kg) − 0.843·t`, R 0.86, SEE 3.37 [S2].
- It is not George's 1-mile jog equation either, which is `100.5 + 8.344·G − 0.1636·BM − 1.438·T − 0.1928·HR` [S1].

### 1.2 What the primary source says [S1, abstract]
- n = 149 (88 men, 61 women), aged 18-29, "relatively fit college students", VO₂max 47.7 ± 6.3.
- The 1.5-mile regression (N = 50) used elapsed run time, body mass and gender.
- Validation: N = 49, r_adj 0.90, SEE 2.8. Cross-validation: N = 47, r_adj 0.82, **SEE 3.9**.
- The coefficients are past the point where the abstract is truncated, and the paper is paywalled [not found in primary].

### 1.3 Coefficients from secondary sources *(superseded; verified against Table 2, see the status section)*
- **kg form:** 88.02 + 3.716·sex − 0.1656·kg − 2.76·min [S3, S4].
- **lb form:** 88.02 + 3.716·sex − 0.0753·lb − 2.767·min [S5].
- They are the same model: 0.0753 × 2.2046 = 0.166 [inferred].
- Sex is coded 1 = male, 0 = female in all of them.

### 1.4 Protocol
- The 1.5-mile run is the classic timed run at maximal effort ("as fast as possible") [S4, S5].
- The ≥ 8-9 minute and HR ≤ 180 restrictions in S1 apply only to the 1-mile jog [S1].

### 1.5 Term by term against `legacy.py:64`

| Term | Source | legacy.py:64 | Problem |
|---|---|---|---|
| Intercept | 88.02 | 88.02 | none |
| Body mass | −0.1656 per kg | −0.1656 × 163 | **Unit error plus hard-code.** If 163 lb (73.9 kg) was meant, the term should be 12.24, not 26.99, so a −14.75 bias. For every athlete the mass is made up [inferred]. |
| Time | minutes | `time_sec/60` | none (2.76 vs 2.767 is negligible) |
| Sex | 1 = male, 0 = female | always 1 | **Hard-coded male.** `MetricsRequest.sex` is ignored [repo legacy.py:132-136, 238]. |
| Age | not a predictor | accepted, ignored | No population gate [repo legacy.py:133]. |
| Input range | young, fit runners | 3 min to 4 h accepted [repo legacy.py:52] | A 4-hour run gives a negative VO₂ [inferred]. |

- Worked example, a 163 lb man running 12:00 [inferred]: legacy **31.6**; George **46.4**; 483/t + 3.5 gives **43.75**.
- The Cooper Institute's 2013 norms put the 50th percentile for men aged 30-39 at 12:25, which is 42.4 [S6]. Legacy therefore grades a median man "Below Average".

## 2. Grade tables

**`vo2_category_male_36_45`** [repo legacy.py:67-78]
- Cut points: ≥ 51 / 43 / 38 / 34 / 30.
- These are the "36-45" men's column of a widely copied table, shifted down by about 1, with the "Very poor" band dropped.
- Topendsports says that table was "derived from several and now unknown sources" and traces it to the ACE PT Manual, adapted from Golding et al. (1986) [S7].
- It is not Cooper or ACSM. The Golding original was not read [not found].

**`result_category_male_30_39`** [repo legacy.py:81-90]
- Cut points: < 10:30 / 12:00 / 13:00 / 15:00, labelled Excellent / Good / Satisfactory / Marginal / Poor.
- No published table with these cut points or labels was found [not found]. "Satisfactory / Marginal" is USAF composite-score wording [S9].
- The authoritative reference is Cooper 2013, men aged 30-39, n = 13,308 [S6]: 80th percentile 10:47, 60th 11:56, 40th 13:04, 20th 14:34.

**Further problems** [inferred]
- The two tables use different age bands (36-45 and 30-39), and both are male-only.
- Cooper's 1.5-mile to VO₂ columns follow 483/t + 3.5. Grading a George-equation VO₂ against them mixes two models.
- **Recommended norm source:** Cooper Institute 2013 [S6]. ACSM reproduces Cooper's percentiles [S8, not read].

## 3. The 300 m + 1.5 mi "fatigue factor"

**What legacy computes** [repo legacy.py:93-104, 239-240]
- `(t300/300) / (t1p5/2400) − 1`, and anything within ±5% is labelled "Balanced".
- 2400 m should be 2414.02 m.
- 60 s with 12:00 gives −33%, and so does 50 s with 10:00. Even a Riegel exponent of 1.06 gives about −12% [S12; inferred]. So almost everyone is labelled "Endurance-biased".

**Published bases**
- **Hinshaw | Sherwood 1.5-mile program** [S10]. It uses exactly the 300 m + 1.5 mi pair, with the formula `LOG(T1/T2)/LOG(D1/D2)` and a norm of about 20% for non-specialists. It describes the 300 m as primarily an anaerobic, speed test. For 60 s with 12:00 the exponent is 1.192, or about 19% [inferred]. This is coaching material, not a validated test.
- **The seeded Hinshaw 400 m→mile formula** [repo derived_metric_formulas.py:13-26; seed_benchmarks.py:513-526] is `100·(t_mile/(t400·4.023) − 1)`, a percentage pace slowdown. It is a different quantity from the exponent, so the two "~20%" norms are not interchangeable [inferred].
- **Anaerobic speed reserve** [S13, S14] is peer-reviewed, but it uses a 50 m sprint plus a 1500 m run in elite middle-distance runners. It does not validate a 300 m to 1.5 mi ratio.
- **Cooper / law-enforcement norms** [S6] score the 300 m (anaerobic power) and the 1.5 mi (cardiorespiratory) separately. That is the likely origin of the pairing [inferred].

**Conclusion:** no published equation uses a 300 m time to estimate VO₂, so the 300 m input has no claim to authority over aerobic capacity.

## 4. Capacity mapping

*This is context for D4. The release gives these records no capacity authority, so none of this mapping applies to them.*

**Path** [repo]
1. `score01 = clamp((vo2 − 25)/45)` [benchmark_service.py:425; state_update_v0.py:139-161; seed_benchmarks.py:73-84].
2. `run_benchmark_operator` takes the generic branch [state_transitions.py:61-73].
3. The mapping targets `capacity.aerobic` as a residual with coefficient 0.9 [seed_benchmarks.py:584-586].
4. The update [state_update_v0.py:217-230, 414-423; parameters.py:233; vectors.py:66-72]:
   - measurement variance = 0.08/0.9
   - K = P/(P + measurement variance)
   - new value = current + 0.9·K·(score01 − current/650)·650
   - P_post = (1 − K)·P

**A fresh athlete** [inferred from state_service.py:82; seed_variance.py:52-56, 65, 133]
- Aerobic capacity 300 and variance P = 0.85 give K = 0.905.
- The effective move is therefore 0.815 of the residual, and P_post = 0.080, below the validated-benchmark seed tier (0.102).

**In plain words**
- 1 ml/kg/min of error is about **11.8 aerobic units**. SEE 3.9 is about ±46 units at one standard deviation.
- After one test, the engine treats the result as nearly certain.
- With legacy's bias, the 163 lb man at 12:00 would go from 300 to about **134**. With the equation applied correctly, he would go to about 307 [inferred].

**Existing authority gates**
- `onboarding_onramp`: `mode_cap` is `initialize_prior` ("onramp seeds, never regresses"), and then `floor_if_raised` applies [repo observation_authority.py mode_cap; benchmark_service.py:493-497, 567-571; state_transitions.py:76-82].
- `manual` or `benchmark_test` sources default to `ad_hoc` collection mode, which allows `bidirectional_update` [observation_authority.py `_SOURCE_TO_COLLECTION_MODE`].
- The default labels are `direct_measurement` / `measured`. These overstate a regression estimate.
  - `EV_PROTOCOL_GRADE_ESTIMATE` exists [strength_evidence.py:37].
  - `EV_MODELED_ESTIMATE` caps authority to none [strength_evidence.py:51].
- The SEE is not represented anywhere: `observation_weight` 0.9 is fixed per benchmark definition [benchmark_service.py:560].

## 5. Recommendations *(superseded by D1–D6; kept as history)*

**[SETTLED by owner]** No historical reseeding. Today `/compute-metrics` writes no state [repo legacy.py:225-230].

### 5.1 Protocol
- **Primary:** a maximal-effort 1.5-mile (2414 m) run on a flat, measured course, timed to the second [S1, S6].
- **Optional:** Larsen 2002's submaximal walk/jog/run at RPE 13, as a separate benchmark definition with its own equation [S2].

### 5.2 Inputs

| Input | Role | Status in the repo |
|---|---|---|
| 1.5 mi time | predictor | collected |
| Body mass (kg) | predictor; replaces the 163 hard-code | `bodyweight_kg` exists [user.py:115; onboarding.py:72] |
| Sex | predictor | collected on screen but sent only to `/compute-metrics`; no sex field in user.py, onboarding.py or profile.py (searched those) |
| Date of birth | population gate and norm band, not a predictor | `date_of_birth` exists [user.py:119]; the screen defaults age to 28 [OnboardingScreen.tsx:241], which must stop |
| 300 m time | optional speed profile only | collected |

### 5.3 Equation and applicability
- Use George 1993's 1.5-mile equation, kg form, once its coefficients are verified against the full text.
- It applies to ages 18-29, relatively fit, VO₂ 47.7 ± 6.3 [S1].
- Record the observation with no effect on capacity when:
  - age is outside 18-29, unless the owner chooses explicit, documented down-weighting instead;
  - sex or body mass is missing (no defaults);
  - the estimate falls outside about 29-67 (mean ± 3 SD) or outside the 25-70 window [thresholds inferred].
- 483/t + 3.5 may be used for display only; its primary source was not found [not found].

### 5.4 Uncertainty
- Record `evidence_type = protocol_grade_estimate` and `observation_model = george1993_1p5mi_run`.
- Note: `value_semantics = estimated` caps evidence to `upward_lower_bound` [observation_authority.py evidence_cap]. The owner has to pick the semantics that give the intended authority.
- Derive the measurement variance from the cross-validated SEE 3.9 (σ = 3.9/45 = 0.087 in score01 units). The engine's variance scale is "compressed relative" [seed_variance.py:48-50], so this needs a calibration decision [OPEN]. At minimum, P_post must not fall below the validated-benchmark tier.
- Persist the SEE, the equation id and the inputs in `replay_input` [benchmark_service.py:629-637].

### 5.5 Collection mode
Keep `onboarding_onramp`, so onboarding can only initialise the prior. Only a later `retest` can update in both directions.

### 5.6 The 300 m input
- Remove it from the VO₂ path.
- Optionally keep it as a separate KPI: the Hinshaw exponent `log(t1p5/t300)/log(2414.02/300)`, or Cooper / law-enforcement 300 m percentiles [S6].
- Do not merge it with `run_fatigue_factor`.
- Delete or rewrite the ±5% `fatigue_profile` band.

### 5.7 Guest tile
Hide it, or relabel it "estimate, not used for your plan". Stop showing male 30-45 grades to everyone [repo legacy.py:252-253].

### 5.8 Seed definition
Set `measurement_protocol.inputs` [seed_benchmarks.py:80-83] to `[run_1p5mi_seconds, bodyweight_kg, sex]`, with age as a gate. No historical reseeding.

### Counter-argument
A strict 18-29 gate denies aerobic authority to most users. The alternative is to accept all ages with a wider variance, based on the 1.5-mile meta-analysis [S15], but that extrapolates the regression outside the population it was fitted on. The lean is to gate capacity authority strictly and show a display-only estimate to everyone else.

## 6. Not verified (as of 2026-10-21)
- **Sex coding for the 1.5-mile model:** inferred, not printed. The owner accepted this as a documented inference.
- **The OCR copy:** read for the coefficients, which appear only in Table 2. Secondary sources agree with them; the lb form was rounded in those sources.
- **Other sources:** the original Golding 1986 and ACE tables, and the ACSM Guidelines text, were not read. They matter only for the removed grades.
- **Any source for the "Satisfactory / Marginal" 1.5-mile table:** none found. That table is removed.
- **S15:** read at abstract level only.

## 8. Sources
- **S1** George JD, Vehrs PR, Allsen PE, Fellingham GW, Fisher AG. VO2max estimation from a submaximal 1-mile track jog for fit college-age individuals. *Med Sci Sports Exerc* 1993;25(3):401-406. PMID 8455458 (https://pubmed.ncbi.nlm.nih.gov/8455458/). The full text was read from https://www.researchgate.net/profile/Gilbert-Fellingham/publication/14744769_VO2_max_estimation_from_a_submaximal_1-mile_track_jog_for_fit_college_age_individuals/links/5a0f209baca27287ce273412/VO2-max-estimation-from-a-submaximal-1-mile-track-jog-for-fit-college-age-individuals.pdf. Equation: Table 2, p. 404. Protocol: p. 403. Sample: Table 1.
- **S2** Larsen GE, George JD, Alexander JL, Fellingham GW, Aldana SG, Parcell AC. Prediction of maximum oxygen consumption from walking, jogging, or running. *Res Q Exerc Sport* 2002;73(1):66-72. doi:10.1080/02701367.2002.10608993.
- **S3** sport-calculator.com; **S4** gymcreek.com; **S5** Marathon Handbook. These are secondary sources for the coefficients.
- **S6** The Cooper Institute (2013), *Physical Fitness Assessments and Norms for Adults and Law Enforcement*; Hoffman & Collingwood (2005), *Fit For Duty*, 2nd ed.
- **S7** Topendsports VO₂max norms (cites the ACE PT Manual / Golding et al. 1986).
- **S8** ACSM's Guidelines for Exercise Testing and Prescription, 11th ed. (2021). Not read.
- **S9** USAF Fitness Assessment categories.
- **S10** Hinshaw C & Sherwood P, 1.5-mile Run Training Program, fatigue factor (aerobiccapacity.com).
- **S11** Hinshaw C, aerobiccapacity.com (400 m to mile fatigue factor).
- **S12** Riegel PS. Athletic records and human endurance. *Am Sci* 1981;69(3):285-290.
- **S13** Sandford GN et al. Anaerobic Speed Reserve: a key component of elite male 800-m running. *IJSPP* 2019;14(4):501-508.
- **S14** Sandford GN, Laursen PB, Buchheit M. Anaerobic speed/power reserve and sport performance. *Sports Med* 2021;51(10):2017-2028.
- **S15** Criterion-related validity of the distance- and time-based walk/run field tests for estimating cardiorespiratory fitness: systematic review and meta-analysis. *PLoS One* 2016 (PMC4795745).
