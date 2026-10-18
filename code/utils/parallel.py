"""
A utility for running tasks in parallel using either threads or processes.
"""

import sys
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import Callable, Optional
from tqdm import tqdm

def run_parallel(worker_func, tasks: list, max_workers: int, executor_type: str = 'thread', description: str = "Processing tasks", timeout: int = None, result_callback: Optional[Callable] = None):
    """
    Runs a worker function over a list of tasks in parallel.

    Args:
        worker_func: The function to execute for each task.
        tasks: A list of tasks. Each task is a dict passed to the worker_func.
        max_workers: The maximum number of parallel workers (threads or processes).
        executor_type: The type of executor to use. Can be 'thread' (default) 
                       or 'process'. Use 'process' for CPU-bound tasks.
        description: A description to show in the progress bar.
        timeout: An optional timeout in seconds for each individual task.
        result_callback: An optional function to call with the result of each
                         completed task.

    Returns:
        A list of results from the worker function.
    """
    if executor_type == 'process':
        Executor = ProcessPoolExecutor
        print(f"Using ProcessPoolExecutor with {max_workers} workers.", file=sys.stderr)
    elif executor_type == 'thread':
        Executor = ThreadPoolExecutor
        print(f"Using ThreadPoolExecutor with {max_workers} workers.", file=sys.stderr)
    else:
        raise ValueError("executor_type must be either 'thread' or 'process'")

    results = []
    with Executor(max_workers=max_workers) as executor:
        # Create a future for each task
        future_to_task = {executor.submit(worker_func, task): task for task in tasks}
        
        # Use tqdm for a progress bar
        progress_bar = tqdm(as_completed(future_to_task), total=len(tasks), desc=description, unit="task")
        
        for future in progress_bar:
            task = future_to_task[future]
            try:
                result = future.result(timeout=timeout)
                results.append(result)
                if result_callback:
                    result_callback(result)
                
                # Optionally update progress bar description with task info
                if 'url' in task:
                    progress_bar.set_postfix_str(task['url'].split('/')[-1])
            except KeyboardInterrupt:
                print(f"Task {task.get('url', 'unknown')} interrupted. Shutting down.", file=sys.stderr)
                raise
            except Exception as exc:
                print(f"Task {task.get('url', 'unknown')} generated an exception: {exc}", file=sys.stderr)
                # Optionally, you could append an error result here
                # results.append({"error": str(exc), "task": task})

    return results