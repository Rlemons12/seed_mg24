#ifndef SEED_MG24_VIBRATION_FILTER_H_
#define SEED_MG24_VIBRATION_FILTER_H_

namespace seed_mg24 {

class FirstOrderHighPass {
 public:
  FirstOrderHighPass();
  bool configure(float sample_rate_hz, float cutoff_hz);
  void reset(float initial_input = 0.0f);
  float apply(float input);
  float coefficient() const { return alpha_; }

 private:
  float alpha_;
  float previous_input_;
  float previous_output_;
  bool initialized_;
};

}  // namespace seed_mg24

#endif
