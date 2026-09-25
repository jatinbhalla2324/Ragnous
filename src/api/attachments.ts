import { Attachment, RejectedAttachment } from '../types/rag';
import { API_BASE } from './config';

/** Mirrors the ceilings in the backend's file_service, so obviously-too-big
 *  files are caught before a 20 MB upload starts. */
export const MAX_FILES = 6;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export const ACCEPTED_TYPES =
  'image/*,application/pdf,.docx,.txt,.md,.csv,.tsv,.json,.xml,.html,.log';

export interface UploadResult {
  attachments: Attachment[];
  rejected: RejectedAttachment[];
}

export const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * Hand the files to the backend, which does the reading — PDF text, Word text,
 * image re-encoding — and give back what the tutor will actually receive.
 *
 * Files are read on drop rather than on send so a broken PDF or an unsupported
 * format is reported while the student is still typing, and so the composer can
 * show a real page count instead of a filename it knows nothing about.
 */
export const uploadAttachments = async (files: File[]): Promise<UploadResult> => {
  const form = new FormData();
  files.forEach(file => form.append('files', file, file.name));

  const res = await fetch(`${API_BASE}/attachments`, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    // 422 means every file failed; the body names each one and why.
    let rejected: RejectedAttachment[] = [];
    try {
      const body = await res.json();
      rejected = body?.detail?.rejected ?? [];
    } catch {
      /* fall through to the generic message below */
    }
    if (rejected.length > 0) return { attachments: [], rejected };

    throw new Error(
      res.status === 413
        ? 'Those files are too large to upload.'
        : `Could not read the files (server said ${res.status}).`
    );
  }

  const data = await res.json();
  return {
    attachments: (data.attachments ?? []) as Attachment[],
    rejected: (data.rejected ?? []) as RejectedAttachment[],
  };
};
