import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { AppError, AppErrorCode } from '../errors/AppError';

export interface StreamHttpResponse {
  responseCode: number;
  result: Uint8Array;
}

export class RequestScope {
  private active?: http.HttpRequest;
  private rejectActive?: (error: AppError) => void;

  cancel(): void {
    const task: http.HttpRequest | undefined = this.active;
    const reject: ((error: AppError) => void) | undefined = this.rejectActive;
    this.active = undefined;
    this.rejectActive = undefined;
    if (task !== undefined) task.destroy();
    if (reject !== undefined) {
      reject(new AppError(AppErrorCode.Cancelled, '操作已取消。'));
    }
  }

  request(url: string, options: http.HttpRequestOptions): Promise<http.HttpResponse> {
    this.cancel();
    const task: http.HttpRequest = http.createHttp();
    return this.requestWithTask(task, url, options);
  }

  requestWithTask(task: http.HttpRequest, url: string,
    options: http.HttpRequestOptions): Promise<http.HttpResponse> {
    this.cancel();
    this.active = task;
    return new Promise<http.HttpResponse>((resolve, reject) => {
      let settled: boolean = false;
      const clear = (): void => {
        if (this.active === task) {
          this.active = undefined;
          this.rejectActive = undefined;
        }
        task.destroy();
      };
      this.rejectActive = (error: AppError): void => {
        if (settled) return;
        settled = true;
        clear();
        reject(error);
      };
      try {
        task.request(url, options, (error: BusinessError, response: http.HttpResponse): void => {
          if (settled) return;
          settled = true;
          clear();
          if (error !== undefined && error !== null) reject(error);
          else resolve(response);
        });
      } catch (error) {
        if (!settled) {
          settled = true;
          clear();
          reject(error instanceof Error ? error : new Error('网络请求启动失败。'));
        }
      }
    });
  }

  requestInStream(url: string, options: http.HttpRequestOptions,
    maxBytes: number): Promise<StreamHttpResponse> {
    this.cancel();
    const task: http.HttpRequest = http.createHttp();
    this.active = task;
    return new Promise<StreamHttpResponse>((resolve, reject) => {
      let settled: boolean = false;
      let responseCode: number = 0;
      let statusReceived: boolean = false;
      let dataEnded: boolean = false;
      let totalBytes: number = 0;
      const chunks: Uint8Array[] = [];
      const clear = (): void => {
        task.off('dataReceive', onDataReceive);
        task.off('dataEnd', onDataEnd);
        if (this.active === task) {
          this.active = undefined;
          this.rejectActive = undefined;
        }
        task.destroy();
      };
      const fail = (error: Object): void => {
        if (settled) return;
        settled = true;
        clear();
        reject(error);
      };
      const finish = (): void => {
        if (settled || !statusReceived || !dataEnded) return;
        settled = true;
        const merged: Uint8Array = new Uint8Array(totalBytes);
        let offset: number = 0;
        chunks.forEach((chunk: Uint8Array) => {
          merged.set(chunk, offset);
          offset += chunk.byteLength;
        });
        clear();
        resolve({ responseCode: responseCode, result: merged });
      };
      const onDataReceive = (data: ArrayBuffer): void => {
        if (settled) return;
        const chunk: Uint8Array = new Uint8Array(data);
        totalBytes += chunk.byteLength;
        if (totalBytes > maxBytes) {
          fail(new Error(`远程响应超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 限制。`));
          return;
        }
        const copy: Uint8Array = new Uint8Array(chunk.byteLength);
        copy.set(chunk);
        chunks.push(copy);
      };
      const onDataEnd = (): void => {
        dataEnded = true;
        finish();
      };
      this.rejectActive = (error: AppError): void => fail(error);
      task.on('dataReceive', onDataReceive);
      task.on('dataEnd', onDataEnd);
      try {
        task.requestInStream(url, options, (error: BusinessError, status: number): void => {
          if (settled) return;
          if (error !== undefined && error !== null) {
            fail(error);
            return;
          }
          responseCode = status;
          statusReceived = true;
          finish();
        });
      } catch (error) {
        fail(error instanceof Error ? error : new Error('流式网络请求启动失败。'));
      }
    });
  }
}
