import type { TranscriptSegment } from '../models/BiliModels';

export class TranscriptService {
  formatTimestamp(totalSeconds: number): string {
    const safeSeconds: number = Math.max(0, Math.floor(totalSeconds));
    const hours: number = Math.floor(safeSeconds / 3600);
    const minutes: number = Math.floor((safeSeconds % 3600) / 60);
    const seconds: number = safeSeconds % 60;
    const minuteText: string = minutes.toString().padStart(2, '0');
    const secondText: string = seconds.toString().padStart(2, '0');
    return hours > 0 ? `${hours}:${minuteText}:${secondText}` : `${minuteText}:${secondText}`;
  }

  toTimestampedText(segments: TranscriptSegment[]): string {
    return segments
      .filter((segment: TranscriptSegment) => segment.content.trim().length > 0)
      .map((segment: TranscriptSegment) =>
        `[${this.formatTimestamp(segment.startSeconds)}] ${segment.content.trim()}`)
      .join('\n');
  }
}

