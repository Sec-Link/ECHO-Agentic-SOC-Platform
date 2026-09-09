export interface WorkflowProgress {
  execution_id: string;
  workflow_id: string;
}

// A null update means the server has subscribed: reload the snapshot, including
// any changes made while disconnected. Use fetch so tokens stay in headers.
export function listenForWorkflowProgress(
  url: string,
  getToken: () => string | null,
  onProgress: (progress: WorkflowProgress | null) => void,
): () => void {
  const controller = new AbortController();
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let retryDelay = 1000;

  const connect = async () => {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const token = getToken();
      const response = await fetch(url, {
        headers: {
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Token ${token}` } : {}),
        },
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) return;
      if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
        throw new Error('Workflow progress stream unavailable');
      }
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let separator: RegExpExecArray | null;
        while ((separator = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, separator.index);
          buffer = buffer.slice(separator.index + separator[0].length);
          let event = '';
          const data: string[] = [];
          for (const line of frame.split(/\r?\n/)) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
          }
          if (controller.signal.aborted) return;
          if (event === 'ready') {
            retryDelay = 1000;
            onProgress(null);
          } else if (event === 'progress') {
            const progress = JSON.parse(data.join('\n'));
            if (progress && typeof progress.execution_id === 'string' && typeof progress.workflow_id === 'string') {
              onProgress(progress);
            }
          }
        }
        if (buffer.length > 65536) throw new Error('Workflow progress event too large');
      }
    } catch {
      // Reconnect and reload a snapshot after transport or decoding errors.
    } finally {
      if (reader) {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    }
    if (!controller.signal.aborted) {
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    }
  };

  void connect();
  return () => {
    controller.abort();
    clearTimeout(retryTimer);
  };
}
