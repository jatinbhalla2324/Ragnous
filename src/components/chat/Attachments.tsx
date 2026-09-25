import { FileText, FileType2, Image as ImageIcon, Loader2, X } from 'lucide-react';
import { Attachment } from '../../types/rag';
import { formatBytes } from '../../api/attachments';

const iconFor = (att: Attachment) => {
  if (att.kind === 'image') return ImageIcon;
  if (att.mime === 'application/pdf') return FileType2;
  return FileText;
};

/** "PDF · 4 pages", "Word document", "312 KB" — whatever is most useful. */
const subtitleFor = (att: Attachment): string => {
  if (att.kind === 'image') return `Image · ${formatBytes(att.size)}`;
  if (att.mime === 'application/pdf') {
    const pages = att.page_count ? `${att.page_count} page${att.page_count === 1 ? '' : 's'}` : null;
    return ['PDF', pages, att.note ? 'scanned' : null].filter(Boolean).join(' · ');
  }
  if (att.mime.includes('wordprocessingml')) return `Word document · ${formatBytes(att.size)}`;
  return `${formatBytes(att.size)}`;
};

interface ChipProps {
  attachment: Attachment;
  onRemove?: () => void;
}

/**
 * One attached file. An image shows its own thumbnail; everything else gets an
 * icon and the two things a student actually wants confirmed — that the right
 * file is attached, and that the tutor could read it.
 */
const AttachmentChip = ({ attachment, onRemove }: ChipProps) => {
  const Icon = iconFor(attachment);
  const isImage = attachment.kind === 'image' && attachment.data_url;

  return (
    <div
      className="group/chip relative flex items-center gap-2.5 h-14 pl-2 pr-3 rounded-xl border border-line bg-surface max-w-[15rem]"
      title={attachment.note || attachment.name}
    >
      {isImage ? (
        <img
          src={attachment.data_url}
          alt={attachment.name}
          className="w-10 h-10 rounded-lg object-cover shrink-0 bg-surface-3"
        />
      ) : (
        <span className="grid place-items-center w-10 h-10 rounded-lg bg-canvas border border-line shrink-0">
          <Icon className="w-[18px] h-[18px] text-ink-2" strokeWidth={1.7} />
        </span>
      )}

      <span className="min-w-0 leading-tight">
        <span className="block text-[0.8125rem] font-medium text-ink truncate">
          {attachment.name}
        </span>
        <span className="block text-[0.6875rem] text-ink-3 truncate">
          {subtitleFor(attachment)}
        </span>
      </span>

      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${attachment.name}`}
          title="Remove"
          className="absolute -top-1.5 -right-1.5 grid place-items-center w-5 h-5 rounded-full bg-ink text-white opacity-0 group-hover/chip:opacity-100 focus:opacity-100 transition-opacity"
        >
          <X className="w-3 h-3" strokeWidth={2.4} />
        </button>
      )}
    </div>
  );
};

interface TrayProps {
  attachments: Attachment[];
  uploadingCount?: number;
  onRemove?: (index: number) => void;
  className?: string;
}

/** The row of files waiting to be sent, shown inside the composer. */
export const AttachmentTray = ({
  attachments,
  uploadingCount = 0,
  onRemove,
  className = '',
}: TrayProps) => {
  if (attachments.length === 0 && uploadingCount === 0) return null;

  return (
    <div className={`flex flex-wrap gap-2 ${className}`}>
      {attachments.map((att, i) => (
        <AttachmentChip
          key={`${att.name}-${i}`}
          attachment={att}
          onRemove={onRemove ? () => onRemove(i) : undefined}
        />
      ))}

      {Array.from({ length: uploadingCount }).map((_, i) => (
        <div
          key={`pending-${i}`}
          className="flex items-center gap-2.5 h-14 pl-2 pr-3 rounded-xl border border-dashed border-line-strong bg-surface"
        >
          <span className="grid place-items-center w-10 h-10 rounded-lg bg-canvas border border-line shrink-0">
            <Loader2 className="w-4 h-4 text-ink-3 animate-spin" strokeWidth={1.9} />
          </span>
          <span className="text-[0.8125rem] text-ink-3">Reading…</span>
        </div>
      ))}
    </div>
  );
};

/** The same files, shown above the student's message once the turn is sent. */
export const MessageAttachments = ({ attachments }: { attachments: Attachment[] }) => {
  if (!attachments?.length) return null;

  return (
    <div className="flex flex-wrap justify-end gap-2 mb-2">
      {attachments.map((att, i) => (
        <AttachmentChip key={`${att.name}-${i}`} attachment={att} />
      ))}
    </div>
  );
};
