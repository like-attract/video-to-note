import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { util } from '@kit.ArkTS';

export interface WbiKeys {
  imageKey: string;
  subKey: string;
}

export interface SignedQuery {
  wts: number;
  wRid: string;
  queryString: string;
}

export class WbiSigner {
  private readonly mixinKeyOrder: number[] = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
  ];

  sign(params: Map<string, string | number>, keys: WbiKeys, nowSeconds: number): SignedQuery {
    const mixedSource: string = keys.imageKey + keys.subKey;
    let mixinKey: string = '';
    this.mixinKeyOrder.forEach((index: number) => {
      if (index < mixedSource.length) {
        mixinKey += mixedSource[index];
      }
    });
    mixinKey = mixinKey.slice(0, 32);

    const signedParams: Map<string, string | number> = new Map(params);
    signedParams.set('wts', nowSeconds);
    const keysSorted: string[] = Array.from(signedParams.keys()).sort();
    const queryParts: string[] = [];
    keysSorted.forEach((key: string) => {
      const rawValue: string = signedParams.get(key)?.toString() || '';
      const filteredValue: string = rawValue.replace(/[!'()*]/g, '');
      queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(filteredValue)}`);
    });
    const queryString: string = queryParts.join('&');
    return {
      wts: nowSeconds,
      wRid: this.md5Hex(queryString + mixinKey),
      queryString: queryString
    };
  }

  private md5Hex(input: string): string {
    const md: cryptoFramework.Md = cryptoFramework.createMd('MD5');
    const bytes: Uint8Array = new util.TextEncoder('utf-8').encodeInto(input);
    md.updateSync({ data: bytes });
    const digest: cryptoFramework.DataBlob = md.digestSync();
    let hex: string = '';
    for (let index: number = 0; index < digest.data.length; index++) {
      hex += digest.data[index].toString(16).padStart(2, '0');
    }
    return hex;
  }
}
