from dataclasses import asdict, FrozenInstanceError
import importlib
import json
import math
import random
import unittest

from laptop_agent.analytics.diagnostics import anomalies, drivers
m = importlib.import_module("laptop_agent.analytics.diagnostics")


def fixture(n=100):
    x = [[(-1.)**i, (1.,1.,-1.,-1.)[i%4]] for i in range(n)]
    y = [10+3*a-2*b for a,b in x]
    return x,y


class DriversTests(unittest.TestCase):
    def test_known_orthogonal_solution_and_tail_predictions(self):
        x,y=fixture()
        r=drivers(x,y,feature_names=['A','B'])
        self.assertTrue(r.enough_data)
        self.assertEqual((r.train_rows,r.test_rows),(80,20))
        self.assertAlmostEqual(r.standardized_coefficients[0],3/math.sqrt(13))
        self.assertAlmostEqual(r.standardized_coefficients[1],-2/math.sqrt(13))
        for a,b in zip(r.predictions,y[80:]): self.assertAlmostEqual(a,b)
        self.assertAlmostEqual(r.out_of_sample_r2,1.)
        self.assertLess(r.mae,1e-12)
        self.assertAlmostEqual(r.baseline_mae,3.)
        for value in r.vif: self.assertAlmostEqual(value,1.)

    def test_tail_never_fits_or_standardizes_and_loss_is_not_hidden(self):
        x,y=fixture()
        before=drivers(x,y)
        y[80:]=[30-2*v for v in y[80:]]
        after=drivers(x,y)
        self.assertEqual(before.standardized_coefficients,after.standardized_coefficients)
        self.assertEqual(before.vif,after.vif)
        self.assertEqual(before.predictions,after.predictions)
        self.assertLess(after.out_of_sample_r2,0)
        self.assertGreater(after.mae,after.baseline_mae)
        x[80:]=[[100*a,100*b] for a,b in x[80:]]
        changed=drivers(x,y)
        self.assertEqual(changed.standardized_coefficients,before.standardized_coefficients)
        self.assertEqual(changed.vif,before.vif)
        self.assertNotEqual(changed.predictions,before.predictions)

    def test_r2_is_measured_against_the_fixed_training_mean(self):
        x,y=fixture()
        y[80:]=[v+2 for v in y[80:]]
        r=drivers(x,y)
        self.assertAlmostEqual(r.out_of_sample_r2,1-4/17)
        self.assertAlmostEqual(r.mae,2)
        self.assertAlmostEqual(r.baseline_mae,3.5)

    def test_units_and_offsets_do_not_change_standardized_coefficients(self):
        x,y=fixture()
        base=drivers(x,y)
        for factor in (1e-200,1e80):
            r=drivers([[a*factor,b*factor] for a,b in x],[v*factor for v in y])
            self.assertTrue(r.enough_data,r.reason)
            for a,b in zip(r.standardized_coefficients,base.standardized_coefficients):
                self.assertAlmostEqual(a,b)
            self.assertAlmostEqual(r.out_of_sample_r2,base.out_of_sample_r2)
        shifted=drivers([[a+1000,b-500] for a,b in x],[v-300 for v in y])
        for a,b in zip(shifted.standardized_coefficients,base.standardized_coefficients):
            self.assertAlmostEqual(a,b)

    def test_small_sample_constant_feature_and_rank_deficiency_are_explicit(self):
        x,y=fixture(8)
        r=drivers(x,y)
        self.assertTrue(r.enough_data)
        self.assertTrue(any('10 training rows' in w for w in r.warnings))
        r=drivers(x[:4],y[:4])
        self.assertFalse(r.enough_data)
        self.assertEqual(r.predictions,())
        self.assertIsNone(r.out_of_sample_r2)
        r=drivers([[a,a] for a,b in fixture()[0]],fixture()[1])
        self.assertFalse(r.enough_data)
        self.assertIn('rank deficient',r.reason)
        r=drivers([[1,a] for a,b in fixture()[0]],fixture()[1])
        self.assertFalse(r.enough_data)
        self.assertIn('constant',r.reason)

    def test_multivariate_collinearity_is_detected_beyond_pair_correlations(self):
        rng=random.Random(781)
        x=[]
        for _ in range(200):
            a,b=rng.gauss(0,1),rng.gauss(0,1)
            x.append([a,b,a+b+rng.gauss(0,.05)])
        y=[2*a-b+rng.gauss(0,.1) for a,b,c in x]
        r=drivers(x,y)
        self.assertTrue(r.enough_data)
        self.assertTrue(all(v>100 for v in r.vif))
        self.assertTrue(any('collinearity' in w for w in r.warnings))

    def test_constant_training_target_refuses_and_r2_needs_baseline_error(self):
        x,y=fixture()
        self.assertFalse(drivers(x,[3.]*100).enough_data)
        y[80:]=[3.]*20
        r=drivers(x,y)
        self.assertTrue(r.enough_data)
        self.assertAlmostEqual(r.out_of_sample_r2,1-(49+13)/49)
        y[80:]=[10.]*20  # fixed training-mean baseline has zero error
        r=drivers(x,y)
        self.assertIsNone(r.out_of_sample_r2)
        self.assertTrue(any('R2 is undefined' in w for w in r.warnings))

    def test_numerically_constant_columns_and_thin_tail_are_explicit(self):
        x,y=fixture()
        x=[[.3 if i%2 else .30000000000000004,b] for i,(a,b) in enumerate(x)]
        self.assertFalse(drivers(x,y).enough_data)
        x,y=fixture()
        self.assertFalse(drivers(x,[.3 if i%2 else .30000000000000004 for i in range(100)]).enough_data)
        r=drivers(x,y,holdout=.02)
        self.assertTrue(r.enough_data)
        self.assertTrue(any('10 held-out' in w for w in r.warnings))
        self.assertFalse(any('10 held-out' in w for w in drivers(x,y).warnings))

    def test_invalid_inputs_are_refused(self):
        cases=[([],[]),([[1]],[]),([[1],[2,3]],[1,2]),([[True]],[1]),
               ([[float('nan')]],[1]),([[1]],[float('inf')]),([[1]],[10**1000]),
               ('bad',[1]),([[1]]*4097,[1]*4097),([[1]*25],[1])]
        for x,y in cases:
            with self.subTest(x=str(x)[:30]),self.assertRaises(ValueError):drivers(x,y)
        x,y=fixture()
        for kw in ({'holdout':0},{'holdout':1},{'holdout':True},{'holdout':float('nan')},
                   {'feature_names':['a','a']},{'feature_names':['a']},{'feature_names':'ab'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError): drivers(x,y,**kw)

    def test_numeric_extrapolation_is_refused_without_nonfinite_json(self):
        x,y=fixture()
        tiny=[[a*1e-300,b*1e-300] for a,b in x]
        tiny[80:]=[[1e100,1e100]]*20
        r=drivers(tiny,y)
        self.assertFalse(r.enough_data)
        self.assertIn('numeric range',r.reason)
        json.dumps(asdict(r),allow_nan=False)

    def test_results_are_immutable_and_input_is_unchanged(self):
        x,y=fixture()
        saved=json.dumps([x,y])
        r=drivers(x,y)
        self.assertEqual(r,drivers(x,y))
        self.assertEqual(saved,json.dumps([x,y]))
        json.dumps(asdict(r),allow_nan=False)
        with self.assertRaises(FrozenInstanceError):r.mae=123


class AnomalyTests(unittest.TestCase):
    def test_signed_mad_scores_and_original_indices(self):
        r=anomalies([-100,-2,-1,0,1,2,100])
        self.assertEqual((r.median,r.mad),(0,2))
        self.assertEqual(r.indices,(0,6))
        self.assertAlmostEqual(r.scores[0],-50*m.MAD_NORMAL)
        self.assertAlmostEqual(r.scores[-1],50*m.MAD_NORMAL)
        self.assertEqual(r.unscored_indices,())
        self.assertEqual(anomalies([-100,-2,-1,0,1,2,100],threshold=40).indices,())

    def test_zero_mad_never_creates_infinity_or_confident_flags(self):
        r=anomalies([1,1,1,1,2])
        self.assertEqual(r.mad,0)
        self.assertEqual(r.scores,(0.,0.,0.,0.,None))
        self.assertEqual(r.indices,())
        self.assertEqual(r.unscored_indices,(4,))
        self.assertTrue(any('MAD is zero' in w for w in r.warnings))
        json.dumps(asdict(r),allow_nan=False)
        self.assertEqual(anomalies([1]*10).scores,(0.,)*10)
        self.assertEqual(anomalies([]).scores,())

    def test_threshold_is_strict_and_scores_are_scale_invariant(self):
        base=anomalies([-100,-2,-1,0,1,2,100])
        self.assertEqual(anomalies([-100,-2,-1,0,1,2,100],threshold=base.scores[-1]).indices,())
        for factor in (1e-200,1e80):
            r=anomalies([v*factor for v in [-100,-2,-1,0,1,2,100]])
            self.assertEqual(r.indices,base.indices)
            for a,b in zip(r.scores,base.scores):self.assertAlmostEqual(a,b)

    def test_validation_and_overflow_have_json_safe_outcomes(self):
        for values in ('bad',[True],[float('nan')],[float('inf')],[10**1000],[1]*4097):
            with self.subTest(v=str(values)[:30]),self.assertRaises(ValueError):anomalies(values)
        for value in (0,-1,True,float('inf')):
            with self.assertRaises(ValueError):anomalies([1,2,3],threshold=value)
        r=anomalies([0,1e-320,2e-320,1e100])
        self.assertEqual(r.unscored_indices,(3,))
        json.dumps(asdict(r),allow_nan=False)
        with self.assertRaises(FrozenInstanceError):r.threshold=1

    def test_synthetic_noise_and_injected_spikes(self):
        rng=random.Random(1234)
        values=[rng.gauss(0,1) for _ in range(1000)]
        values[21]=15
        values[903]=-15
        r=anomalies(values)
        self.assertIn(21,r.indices)
        self.assertIn(903,r.indices)
        self.assertLess(len(r.indices),12)
