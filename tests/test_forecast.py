from __future__ import annotations

from dataclasses import asdict
import importlib
import json
import math
import random
from statistics import fmean
import unittest
from unittest.mock import patch

f = importlib.import_module("laptop_agent.analytics.forecast")


def series(kind, seed, count):
    rng = random.Random(seed)
    return [30 + (0.18*i if kind in {"trend","season"} else 0)
            + (6*math.sin(2*math.pi*i/7) if kind == "season" else 0)
            + rng.gauss(0,1) for i in range(count)]


class ForecastTests(unittest.TestCase):
    def test_invalid_inputs_are_rejected_without_coercion(self):
        for data in ([1]*7+[float("nan")], [float("inf")]*8, [True]*8,
                     ["1"]*8, [None]*8, "12345678", [10**101]*8, [10**1000]*8, [1]*4097):
            with self.subTest(data=str(data)[:40]), self.assertRaises(ValueError):
                f.forecast(data)
        for kwargs in ({"horizon":0},{"horizon":49},{"horizon":True},{"horizon":1.5},
                       {"level":0},{"level":1},{"level":float("nan")},{"level":True},{"level":10**1000},
                       {"season":1},{"season":-1},{"season":121},{"season":True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                f.forecast([1]*20,**kwargs)

    def test_short_series_and_seasons_do_not_claim_a_model(self):
        for count in range(8):
            result=f.forecast(list(range(count)),3)
            self.assertFalse(result.enough_data)
            self.assertIsNone(result.mase)
            self.assertTrue(all(v is None for v in result.lower))
            self.assertIn("8 observations",result.reason)
        result=f.forecast(list(range(13)),season=7)
        self.assertFalse(result.enough_data)
        self.assertIn("two complete seasons",result.reason)
        result=f.forecast(list(range(20)),48)
        self.assertFalse(result.enough_data)
        self.assertIn("rolling origins",result.reason)

    def test_constant_series_keeps_baseline_and_explains_undefined_mase(self):
        result=f.forecast([4.0]*120,4)
        self.assertEqual(result.method,"naive")
        self.assertEqual(result.points,(4.0,)*4)
        self.assertIsNone(result.season)
        self.assertIsNone(result.mase)
        self.assertIsNone(result.baseline_mase)
        self.assertEqual(result.mae,0)
        self.assertIn("undefined",result.reason)
        self.assertEqual(result.lower,result.points)
        json.dumps(asdict(result),allow_nan=False)

    def test_perfect_trend_is_extrapolated_and_not_called_seasonal(self):
        result=f.forecast([3+2*i for i in range(90)],5)
        self.assertEqual(result.method,"holt")
        self.assertIsNone(result.season)
        self.assertEqual(result.points,tuple(3+2*i for i in range(90,95)))
        self.assertLess(result.mase,result.baseline_mase)

    def test_autocorrelation_finds_period_and_ignores_deterministic_trend(self):
        self.assertEqual(f.detect_season([i*.2 + [0,5,-4,2][i%4] for i in range(96)]),4)
        self.assertIsNone(f.detect_season([i*.2 for i in range(96)]))
        self.assertIsNone(f.detect_season([1]*96))
        self.assertIsNone(f.detect_season([1,2,3]))
        result=f.forecast(series("season",43,180),4,season=0)
        self.assertIsNone(result.season)
        self.assertEqual(result.baseline_method,"naive")

    def test_seasonal_naive_is_a_competitor_not_only_last_value(self):
        values=[0,5,-4,2]*40
        result=f.forecast(values,6,season=4)
        self.assertEqual(result.method,"seasonal_naive")
        self.assertEqual(result.points,(0.,5.,-4.,2.,0.,5.))
        self.assertEqual(result.baseline_mae,0)
        self.assertIn("kept the baseline",result.reason)

    def test_additive_holt_winters_handles_trend_and_phase(self):
        values=[10+.25*i+[0,5,-4,2][i%4] for i in range(120)]
        result=f.forecast(values,6,season=4)
        self.assertEqual(result.method,"holt_winters")
        for h,actual in enumerate(result.points):
            self.assertAlmostEqual(actual,10+.25*(120+h)+[0,5,-4,2][h%4],places=8)

    def test_ses_improves_a_noisy_level(self):
        result=f.forecast(series("noise",19,180),6,season=0)
        self.assertEqual(result.method,"ses")
        self.assertLess(result.mase,result.baseline_mase)

    def test_parameters_and_season_never_see_selection_or_calibration_future(self):
        values=series("season",7,180)
        initial=len(values)//3
        with patch.object(f,"detect_season",wraps=f.detect_season) as detect, \
             patch.object(f,"_tune",wraps=f._tune) as tune, \
             patch.object(f,"_errors",wraps=f._errors) as errors:
            a=f.forecast(values,6)
        self.assertEqual(detect.call_args.args[0],tuple(values[:initial]))
        self.assertTrue(all(call.args[0]==tuple(values[:initial]) for call in tune.call_args_list))
        selection=[call for call in errors.call_args_list if max(call.args[2])<120]
        calibration=[call for call in errors.call_args_list if min(call.args[2])>=120]
        self.assertTrue(selection)
        self.assertEqual(len(calibration),1)
        self.assertTrue(all(max(call.args[2])+6<=120 for call in selection))
        changed=values[:120]+[v+50 for v in values[120:]]
        b=f.forecast(changed,6)
        self.assertEqual((a.method,a.season,a.mase,a.baseline_mase),
                         (b.method,b.season,b.mase,b.baseline_mase))
        self.assertNotEqual(a.points,b.points)

    def test_each_prediction_trains_on_exactly_its_origin_prefix(self):
        values=tuple(float(i) for i in range(20))
        with patch.object(f,"_predict",return_value=(0.,)*3) as predict:
            errors=f._errors(values,f._Spec("naive"),(8,12),3)
        self.assertEqual([c.args[0] for c in predict.call_args_list],[values[:8],values[:12]])
        self.assertEqual(errors,((8.,12.),(9.,13.),(10.,14.)))

    def test_mase_uses_only_training_scale_per_origin(self):
        values=tuple(float(i*i) for i in range(60))
        result=f.forecast(values,2,season=0)
        origins=range(20,39)
        spec=f._Spec("naive")
        ratios=[]
        for origin in origins:
            scale=fmean(abs(values[i]-values[i-1]) for i in range(1,origin))
            ratios.extend(abs(values[origin+h]-values[origin-1])/scale for h in range(2))
        self.assertAlmostEqual(result.baseline_mase,fmean(ratios))

    def test_empirical_quantiles_are_per_horizon_not_normal_or_pooled(self):
        original=f._errors
        rows=(tuple(range(-20,20)),tuple(range(20,60)))
        def errors(y,spec,origins,horizon):
            return rows if min(origins)>=80 else original(y,spec,origins,horizon)
        with patch.object(f,"_errors",side_effect=errors):
            result=f.forecast(series("noise",9,120),2,season=0)
        self.assertAlmostEqual(result.lower[0]-result.points[0],-16.1)
        self.assertAlmostEqual(result.upper[0]-result.points[0],15.1)
        self.assertAlmostEqual(result.lower[1]-result.points[1],23.9)
        self.assertAlmostEqual(result.upper[1]-result.points[1],55.1)

    def test_sparse_calibration_does_not_assert_an_interval(self):
        result=f.forecast(list(range(12)),2)
        self.assertTrue(result.enough_data)
        self.assertTrue(all(value is None for value in result.lower+result.upper))
        self.assertIn("calibration origins",result.interval_reason)
        self.assertTrue(all(v is None for v in f.forecast(list(range(120)),level=.99).lower))

    def test_units_do_not_change_season_or_selection(self):
        values=series("season",88,150)
        reference=f.forecast(values,3)
        for factor in (1e-200, 1e80):
            result=f.forecast([v*factor for v in values],3)
            self.assertEqual((result.method,result.season),(reference.method,reference.season))
            for actual,expected in zip(result.points,reference.points):
                self.assertAlmostEqual(actual/factor,expected,places=8)

    def test_results_are_deterministic_immutable_and_json_safe(self):
        values=series("season",88,150)
        copied=values[:]
        a=f.forecast(values,6)
        self.assertEqual(a,f.forecast(values,6))
        self.assertEqual(values,copied)
        json.dumps(asdict(a),allow_nan=False)
        with self.assertRaises(AttributeError):a.method="wrong"
        # A near-zero training scale followed by a jump must not leak Infinity to JSON.
        extreme=[1e-320*(i%2) for i in range(50)]+[1e100]*70
        json.dumps(asdict(f.forecast(extreme,3,season=0)),allow_nan=False)

    def test_selection_never_loses_to_baseline_and_heldout_coverage(self):
        # Seeds and future observations are independent of tuning/calibration. This is
        # an ensemble check, not a promised coverage rate for arbitrary user data.
        counts={kind:[0,0] for kind in ("trend","season","noise")}
        losses={kind:[[],[]] for kind in counts}
        for kind in counts:
            for seed in range(30):
                values=series(kind,1000+seed,186)
                result=f.forecast(values[:180],6)
                self.assertTrue(result.enough_data)
                self.assertLessEqual(result.mase,result.baseline_mase+1e-12)
                baseline=f._predict(values[:180],6,f._Spec(result.baseline_method,result.season or 0))
                for value,point,naive,low,high in zip(values[180:],result.points,baseline,result.lower,result.upper):
                    counts[kind][0]+=int(low<=value<=high)
                    counts[kind][1]+=1
                    losses[kind][0].append(abs(value-point))
                    losses[kind][1].append(abs(value-naive))
        for kind,(covered,total) in counts.items():
            coverage=covered/total
            self.assertGreaterEqual(coverage,.70,(kind,counts))
            self.assertLessEqual(coverage,.90,(kind,counts))
            self.assertLessEqual(fmean(losses[kind][0]),fmean(losses[kind][1]),kind)
