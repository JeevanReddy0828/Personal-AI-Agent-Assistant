from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import laptop_agent.metrics as metrics

# Compact extract captured non-elevated on 2026-10-01. Keep process-level samples:
# aggregating only the largest sample would miss other processes on the same engine.
_CAPTURED = r'''{"samples":[
 {"InstanceName":"pid_12988_luid_0x00000000_0x000117f9_phys_0_eng_0_engtype_3d","CookedValue":0.12226631839897116,"Status":0},
 {"InstanceName":"pid_38716_luid_0x00000000_0x000117f9_phys_0_eng_0_engtype_3d","CookedValue":0.8105193028900066,"Status":0},
 {"InstanceName":"pid_20596_luid_0x00000000_0x000120dd_phys_0_eng_0_engtype_3d","CookedValue":0.0,"Status":0},
 {"InstanceName":"pid_20596_luid_0x00000000_0x000120dd_phys_0_eng_10_engtype_3d","CookedValue":0.0,"Status":0},
 {"InstanceName":"luid_0x00000000_0x000117f9_phys_0","CookedValue":436817920.0,"Status":0},
 {"InstanceName":"luid_0x00000000_0x000120dd_phys_0","CookedValue":0.0,"Status":0}
],"failed":false}'''
_ADAPTERS = {(0,0x117f9):("AMD Radeon(TM) Graphics",520060928)}


def result(stdout="",returncode=0):
    return SimpleNamespace(stdout=stdout,returncode=returncode)


class GpuTests(unittest.TestCase):
    def setUp(self):
        for fixture in (patch.object(metrics.sys,"platform","win32"),
                        patch.object(metrics,"_reported_failures",set()),
                        patch.object(metrics,"record_failure")):
            fixture.start();self.addCleanup(fixture.stop)
        self.names=Mock(return_value=_ADAPTERS)

    def probe(self,runner,smi=False):
        def which(name):
            return name if name=="powershell" or (smi and name=="nvidia-smi") else None
        with patch.object(metrics.shutil,"which",side_effect=which):
            return metrics._gpu(runner=runner,adapter_reader=self.names)

    def test_captured_samples_match_luid_names_memory_and_zero_usage(self):
        runner=Mock(return_value=result(_CAPTURED))
        actual=self.probe(runner)
        self.assertEqual(actual,[
            {"name":"AMD Radeon(TM) Graphics","util_percent":0.9,"mem_used_mb":416.6,"mem_total_mb":496},
            {"name":"GPU 2","util_percent":0.0,"mem_used_mb":0.0,"mem_total_mb":None}])
        command=runner.call_args.args[0]
        self.assertEqual(command[:3],["powershell","-NoProfile","-NonInteractive"])
        self.assertIn("GPU Engine(*engtype_3D)",command[-1])
        self.assertIn("GPU Adapter Memory(*)",command[-1])
        self.assertEqual(runner.call_args.kwargs["timeout"],6)
        self.assertEqual(runner.call_args.kwargs["creationflags"],getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.assertEqual(runner.call_args.kwargs["encoding"],"utf-8")
        self.assertEqual(runner.call_count,1)
        metrics.record_failure.assert_not_called()

    def test_sums_processes_then_uses_busiest_engine_and_clamps(self):
        data=json.loads(_CAPTURED)
        data["samples"][0]["CookedValue"]=40
        data["samples"][1]["CookedValue"]=35
        data["samples"].append(dict(data["samples"][0],InstanceName="pid_1_luid_0x00000000_0x000117f9_phys_0_eng_1_engtype_3d",CookedValue=60))
        data["samples"][2]["CookedValue"]=120
        data["samples"].append(copy.deepcopy(data["samples"][0]))
        actual=self.probe(Mock(return_value=result(json.dumps(data))))
        self.assertEqual([gpu["util_percent"] for gpu in actual],[75,100])

    def test_names_are_matched_by_luid_not_order_and_memory_sums_physical_adapters(self):
        self.names.return_value={(0,0x120dd):("Second card",8*1048576),(0,0x117f9):("First card",2*1048576)}
        data=json.loads(_CAPTURED)
        data["samples"].append({"InstanceName":"luid_0x00000000_0x000120dd_phys_1","CookedValue":2097152,"Status":1})
        data["samples"].append(copy.deepcopy(data["samples"][-1]))
        actual=self.probe(Mock(return_value=result(json.dumps(data))))
        self.assertEqual([g["name"] for g in actual],["First card","Second card"])
        self.assertEqual(actual[1]["mem_used_mb"],2)
        self.assertEqual(actual[1]["mem_total_mb"],8)

    def test_missing_fields_stay_unknown_and_bad_samples_are_not_zeroes(self):
        data=json.loads(_CAPTURED)
        data["samples"]=[data["samples"][0],data["samples"][5],
                         {"InstanceName":"luid_0x00000000_0x000117f9_phys_0","CookedValue":-1,"Status":0},
                         {"InstanceName":"luid_0x00000000_0x000120dd_phys_1","CookedValue":2,"Status":3221228486}]
        data["failed"]=True
        actual=self.probe(Mock(return_value=result(json.dumps(data))))
        self.assertIsNone(actual[0]["mem_used_mb"])
        self.assertIsNone(actual[1]["util_percent"])
        self.assertEqual(actual[1]["mem_used_mb"],0)
        self.assertEqual(metrics.record_failure.call_count,3)
        for bad in (float("nan"),float("inf")):
            data["samples"][0]["CookedValue"]=bad
            self.assertEqual(len(self.probe(Mock(return_value=result(json.dumps(data))))),1)

    def test_nvidia_success_has_priority_and_never_reads_counters_or_names(self):
        runner=Mock(return_value=result("NVIDIA GPU, 15, 512, 4096\n"))
        self.assertEqual(self.probe(runner,smi=True),[{"name":"NVIDIA GPU","util_percent":15.0,"mem_used_mb":512,"mem_total_mb":4096}])
        runner.assert_called_once();self.names.assert_not_called()
        metrics.record_failure.assert_not_called()

    def test_nvidia_errors_timeouts_empty_and_malformed_responses_fall_back(self):
        for failed in (result("NVIDIA GPU, 15, 512, 4096",1),result(""),result("GPU, N/A, 1, 2"),
                       OSError("denied"),subprocess.TimeoutExpired("nvidia-smi",6)):
            with self.subTest(failed=type(failed).__name__):
                runner=Mock(side_effect=[failed,result(_CAPTURED)])
                self.assertEqual(len(self.probe(runner,smi=True)),2)
                self.assertEqual([c.args[0][0] for c in runner.call_args_list],["nvidia-smi","powershell"])

    def test_counter_failures_are_recorded_once_per_cause_without_raw_output(self):
        for failed in (result("private stdout",1),result("private not-json"),
                       subprocess.TimeoutExpired("private command",6)):
            for _ in range(3):
                runner=Mock(side_effect=failed) if isinstance(failed,Exception) else Mock(return_value=failed)
                self.assertEqual(self.probe(runner),[])
        self.assertEqual(metrics.record_failure.call_count,3)
        self.assertNotIn("private",str(metrics.record_failure.call_args_list))

    def test_absent_powershell_empty_counters_and_unknown_names_degrade(self):
        with patch.object(metrics.shutil,"which",return_value=None):
            for _ in range(2):self.assertEqual(metrics._gpu(runner=Mock()),[])
        self.assertEqual(metrics.record_failure.call_count,1)
        self.assertEqual(self.probe(Mock(return_value=result('{"samples":[]}'))),[])
        self.names.return_value={}
        actual=self.probe(Mock(return_value=result(_CAPTURED)))
        self.assertEqual([g["name"] for g in actual],["GPU 1","GPU 2"])
        self.assertTrue(all(g["mem_total_mb"] is None for g in actual))

    def test_other_platforms_never_probe_windows(self):
        with patch.object(metrics.sys,"platform","linux"):
            runner=Mock()
            self.assertEqual(self.probe(runner),[])
            runner.assert_not_called();self.names.assert_not_called()
            runner.return_value=result("GPU, 12, 3, 4")
            self.assertEqual(self.probe(runner,smi=True)[0]["util_percent"],12)
            self.assertNotIn("creationflags",runner.call_args.kwargs)

    def test_failure_deduplication_is_thread_safe(self):
        threads=[threading.Thread(target=metrics._failure_once,args=("gpu-counters","same cause")) for _ in range(12)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(1)
        metrics.record_failure.assert_called_once_with("metrics/gpu-counters","same cause")


class MetricsCacheTests(unittest.TestCase):
    def setUp(self):
        if metrics._refresh_thread:
            metrics._refresh_thread.join(20)
            self.assertFalse(metrics._refresh_thread.is_alive())
        self.collect=Mock(return_value={"cpu_percent":12.5,"gpus":[{"name":"test"}]})
        for fixture in (patch.object(metrics,"_collect_system_metrics",self.collect),
                        patch.object(metrics.sys,"platform","win32"),
                        patch.object(metrics,"record_failure"),patch.object(metrics,"_reported_failures",set())):
            fixture.start();self.addCleanup(fixture.stop)
        metrics._cached_metrics=None;metrics._cached_at=0;metrics._refresh_thread=None
        self.addCleanup(self.finish)

    def finish(self):
        if metrics._refresh_thread:
            metrics._refresh_thread.join(2)
            self.assertFalse(metrics._refresh_thread.is_alive())
        metrics._cached_metrics=None;metrics._cached_at=0;metrics._refresh_thread=None

    def test_second_call_within_ttl_is_cached(self):
        first=metrics.system_metrics(force=True)
        self.assertEqual(metrics.system_metrics(),first)
        self.collect.assert_called_once()

    def test_force_bypasses_cache(self):
        metrics.system_metrics(force=True);metrics.system_metrics(force=True)
        self.assertEqual(self.collect.call_count,2)

    def test_returns_independent_copy(self):
        first=metrics.system_metrics(force=True)
        first["cpu_percent"]=999;first["gpus"].append({"name":"mutated"})
        second=metrics.system_metrics()
        self.assertEqual(second["cpu_percent"],12.5)
        self.assertEqual(len(second["gpus"]),1)

    def test_cold_and_stale_reads_do_not_wait_or_start_duplicate_workers(self):
        for cold in (True,False):
            with self.subTest(cold=cold):
                entered,release=threading.Event(),threading.Event()
                def slow():
                    entered.set();release.wait(1)
                    return {"cpu_percent":35,"gpus":[{"name":"fresh"}]}
                self.collect.side_effect=slow
                metrics._cached_metrics=None if cold else {"cpu_percent":12,"gpus":[{"name":"stale"}]}
                metrics._cached_at=0
                before=time.perf_counter()
                old=metrics.system_metrics()
                try:
                    self.assertLess(time.perf_counter()-before,.2,"request waited for its probe")
                    self.assertTrue(entered.wait(.5))
                    worker=metrics._refresh_thread
                    for _ in range(10):self.assertEqual(metrics.system_metrics(),old)
                    self.assertIs(metrics._refresh_thread,worker)
                    self.assertEqual(old["cpu_percent"],None if cold else 12)
                    old["gpus"].append({"name":"changed by caller"})
                finally:
                    release.set()
                    if metrics._refresh_thread:
                        metrics._refresh_thread.join(2)
                self.assertEqual(metrics.system_metrics()["gpus"],[{"name":"fresh"}])
        self.assertEqual(self.collect.call_count,2)

    def test_failed_refresh_retains_snapshot_and_does_not_retry_each_request(self):
        metrics.system_metrics(force=True)
        self.collect.side_effect=OSError("failure")
        for _ in range(2):self.assertEqual(metrics.system_metrics(force=True)["cpu_percent"],12.5)
        metrics.record_failure.assert_called_once_with("metrics/refresh","OSError")
        calls=self.collect.call_count
        metrics.system_metrics();self.assertEqual(self.collect.call_count,calls)
        metrics._cached_metrics=None
        metrics.system_metrics(force=True)
        self.assertEqual(metrics.system_metrics(),metrics._empty_metrics())
        self.assertEqual(self.collect.call_count,calls+1)

    def test_non_windows_keeps_synchronous_initial_snapshot(self):
        with patch.object(metrics.sys,"platform","linux"):
            self.assertEqual(metrics.system_metrics()["cpu_percent"],12.5)
            self.assertIsNone(metrics._refresh_thread)
            metrics.system_metrics();self.collect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
