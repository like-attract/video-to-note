import { http } from '@kit.NetworkKit';
import { BusinessError } from '@kit.BasicServicesKit';
import { AppError, AppErrorCode } from '../errors/AppError';

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
}
