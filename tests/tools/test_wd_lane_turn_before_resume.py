"""Constructor timing/cleanup with strict managed fakes, never native execution."""
import json
import re
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q


SCRIPT = REBOOT / 'Invoke-WdLaneTurnLoop.ps1'
NATIVE_NAMES = {
    'CreateJobObject', 'SetInformationJobObject', 'QueryInformationJobObject',
    'QueryJobProcessIds', 'OpenProcess', 'IsProcessInJob',
    'QueryFullProcessImageName', 'AssignProcessToJobObject',
    'TerminateJobObject', 'CreateFile', 'CreateProcess', 'ResumeThread',
    'WaitForSingleObject', 'GetExitCodeProcess', 'TerminateProcess', 'CloseHandle',
}
DECLARATION = re.compile(r'\[DllImport\([^\]]*\)\]\s*(?P<declaration>static extern [^;]+;)', re.S)


def _actual_type_source():
    return SCRIPT.read_text(encoding='utf-8').split("Add-Type -TypeDefinition @'\n", 1)[1].split("\n'@", 1)[0]


def _fake_type_source(source):
    declarations = list(DECLARATION.finditer(source))
    names = [re.search(r'(\w+)\s*\(', item['declaration']).group(1) for item in declarations]
    assert len(names) == len(NATIVE_NAMES) and set(names) == NATIVE_NAMES
    bodies = {
        'CreateJobObject': 'FakeTrace.Add("create_job"); return new IntPtr(101);',
        'SetInformationJobObject': '''
            if(job.ToInt32()!=101 || kind!=9 || info.basic.flags!=0x2000)
                throw new InvalidOperationException("Job containment changed");
            FakeTrace.Add("kill_on_job_close"); return true;''',
        'CreateFile': 'FakeFileCount++; FakeTrace.Add("open:"+FakeFileCount); return new IntPtr(400+FakeFileCount);',
        'CreateProcess': '''
            if(!inherit || flags!=0x08000004) throw new InvalidOperationException("Suspended creation changed");
            FakeTrace.Add("create_suspended");
            pi=new PROCESS_INFORMATION {process=new IntPtr(201),thread=new IntPtr(301),pid=4242,tid=4243};
            return true;''',
        'AssignProcessToJobObject': '''
            if(job.ToInt32()!=101 || process.ToInt32()!=201) throw new InvalidOperationException("Wrong assignment handles");
            FakeTrace.Add("assign"); return FakeMode!="assign_fail";''',
        'ResumeThread': '''
            if(thread.ToInt32()!=301) throw new InvalidOperationException("Wrong thread");
            FakeTrace.Add("resume"); return FakeMode=="resume_fail" ? 0xffffffff : 1;''',
        'TerminateProcess': '''
            if(process.ToInt32()!=201 || code!=125) throw new InvalidOperationException("Wrong termination");
            FakeTrace.Add("terminate:125"); return true;''',
        'WaitForSingleObject': '''
            if(handle.ToInt32()!=201 || ms!=5000) throw new InvalidOperationException("Wrong cleanup wait");
            FakeTrace.Add("wait:5000"); return 0;''',
        'CloseHandle': 'FakeTrace.Add("close:"+handle.ToInt32()); return true;',
    }

    def replace(item):
        declaration = item['declaration']
        name = re.search(r'(\w+)\s*\(', declaration).group(1)
        body = bodies.get(name, 'throw new InvalidOperationException("Unexpected fake call: '+name+'");')
        return declaration.replace('static extern ', 'static ', 1).rstrip(';')+' {'+body+'}'

    fake = DECLARATION.sub(replace, source)
    # Retrieve a fixed synthetic error, not the host thread's native last error.
    fake = fake.replace('Marshal.GetLastWin32Error()', 'FakeLastError()')
    state = '''public static string FakeMode;
    public static int FakeFileCount;
    static int FakeLastError() {return 5;}
    public static readonly System.Collections.Generic.List<string> FakeTrace = new System.Collections.Generic.List<string>();
    IntPtr job,process;'''
    assert fake.count('IntPtr job,process;') == 1
    fake = fake.replace('IntPtr job,process;', state)
    # Fail closed BEFORE Add-Type: no declaration or real-API fallback survives.
    assert not re.search(r'DllImport|\bextern\b|kernel32|NativeLibrary|GetLastWin32Error', fake)
    return fake


HARNESS = r'''
public sealed class FakeTurnResult {
    public string[] Trace;
    public bool Completed, SameObserverException;
    public string ErrorType;
}
public static class FakeTurnHarness {
    public static FakeTurnResult Run(string mode) {
        WdManagedTurnProcess.FakeMode=mode;
        WdManagedTurnProcess.FakeFileCount=0;
        WdManagedTurnProcess.FakeTrace.Clear();
        var result=new FakeTurnResult();
        var sentinel=new InvalidOperationException("synthetic observer failure");
        WdManagedTurnProcess value=null;
        try {
            if(mode=="legacy") value=new WdManagedTurnProcess("synthetic",new string[]{"arg"},"cwd","in","out","err");
            else {
                var signature=new Type[]{typeof(string),typeof(string[]),typeof(string),typeof(string),typeof(string),typeof(string),typeof(Action<int>)};
                var constructor=typeof(WdManagedTurnProcess).GetConstructor(signature);
                if(constructor==null) throw new MissingMethodException("Seven-argument observer constructor absent");
                Action<int> observer=delegate(int pid) {
                    WdManagedTurnProcess.FakeTrace.Add("observer:"+pid);
                    if(mode=="observer_throw") throw sentinel;
                };
                value=(WdManagedTurnProcess)constructor.Invoke(new object[]{"synthetic",new string[]{"arg"},"cwd","in","out","err",mode=="seven_null" ? null : observer});
            }
            result.Completed=true;
        } catch(Exception error) {
            var wrapped=error as System.Reflection.TargetInvocationException;
            if(wrapped!=null) error=wrapped.InnerException;
            result.ErrorType=error.GetType().Name;
            result.SameObserverException=Object.ReferenceEquals(error,sentinel);
        } finally {if(value!=null) value.Dispose();}
        result.Trace=WdManagedTurnProcess.FakeTrace.ToArray(); return result;
    }
}
'''


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_turn_script_parses_without_loading_or_running_it(ps):
    script = ("$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("+
              q(SCRIPT)+",[ref]$tokens,[ref]$errors);@{errors=@($errors|ForEach-Object {$_.Message})}|ConvertTo-Json")
    assert json.loads(_run_powershell(script, executable=ps).stdout)['errors'] == []


@pytest.mark.parametrize('change', ['unknown_import', 'unmatched_extern'])
def test_fake_builder_refuses_unrecognized_native_declarations(change):
    source = _actual_type_source()
    if change == 'unknown_import':
        source = source.replace('static extern IntPtr CreateJobObject(', 'static extern IntPtr UnknownNative(')
    else:
        source += '\npublic class Unsafe { static extern void UnmatchedNative(); }'
    with pytest.raises(AssertionError):
        _fake_type_source(source)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('mode', ['legacy', 'seven_null', 'observed', 'observer_throw', 'assign_fail', 'resume_fail'])
def test_constructor_before_resume_order_and_cleanup_with_strict_fakes(ps, mode):
    # Only extracted C# is compiled, after every native declaration was replaced.
    # No script dot-source, real process/job, producer, token or native workload.
    source = _fake_type_source(_actual_type_source()) + HARNESS
    assert not re.search(r'DllImport|\bextern\b|kernel32|NativeLibrary', source)
    completed = _run_powershell(
        'Add-Type -TypeDefinition '+q(source)+'\n[FakeTurnHarness]::Run('+q(mode)+')|ConvertTo-Json -Depth 5',
        executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    prefix = ['create_job', 'kill_on_job_close', 'open:1', 'open:2', 'open:3', 'create_suspended', 'assign']
    temporary = ['close:401', 'close:402', 'close:403', 'close:301']
    disposed = ['close:101', 'close:201']
    if mode in ('legacy', 'seven_null', 'observed'):
        observer = ['observer:4242'] if mode == 'observed' else []
        assert value['Trace'] == prefix + observer + ['resume'] + temporary + disposed
        assert value['Completed'] and value['ErrorType'] is None
    else:
        observer = [] if mode == 'assign_fail' else ['observer:4242']
        resume = ['resume'] if mode == 'resume_fail' else []
        assert value['Trace'] == prefix + observer + resume + ['terminate:125', 'wait:5000'] + disposed + temporary
        assert not value['Completed']
        assert value['ErrorType'] == ('InvalidOperationException' if mode == 'observer_throw' else 'Win32Exception')
        assert value['SameObserverException'] is (mode == 'observer_throw')
